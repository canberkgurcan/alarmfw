# alarmfw

OpenShift/K8s pod sağlığını periyodik olarak kontrol eden, durum değişimlerini dedup'layıp Zabbix/SMTP/outbox üzerinden alarm gönderen Python runner. Stateless servis değil — durumu SQLite'ta tutar.

## İstek Akışı

```
main.py run --config base.yaml
    │
    ├─ load_config()            includes[] → _deep_merge → ${ENV} expand
    │
    └─ run_all(cfg)
         store  = SqliteStateStore(state_db)
         policy = DedupPolicy.from_config(cfg)
         fanout = NotifierFanout(cfg)
         │
         for check in cfg["checks"] (enabled olanlar):
              runner = _load_check_runner(type)   # CHECK_REGISTRY[type] → import → .run
              raw    = runner({**params, "alarm_name": name})
              results = raw if list else [raw]     # CheckResult | List[CheckResult]
              │
              for result in results:
                   _process_result()
                        _should_notify()           # dedup kararı
                        active_silence()            # maintenance bastırma
                        fanout.send_with_fallback() # primary → fallback
                        store.upsert()              # yeni state kaydet
```

Daemon modu `runtime.interval_sec > 0` ise açılır; aksi halde tek sefer çalışıp çıkar. Tek instance garantisi `FileLock` ile (ikinci instance → exit 3).

## Status & Exit Kontratı — Bunu Bozma

Üç durum vardır ve davranışları farklıdır:

| Status | Anlamı | Notifier davranışı |
|---|---|---|
| `OK` | Sorun yok | Bildirim yok (yalnız recovery'de gider) |
| `PROBLEM` | Gerçek sorun | primary → fallback |
| `ERROR` | Check'in kendisi çöktü | **Sadece primary** — SMTP fallback'e GİTMEZ |

`_process_result` içinde `effective_fallback = [] if status == ERROR else fallback`. ERROR'da mail spam'ini önlemek için kasıtlıdır — kaldırma.

`run_all` exit code: `0`=tümü OK, `1`=en az bir PROBLEM/ERROR var, `2`=notify başarısız. Bu kod monitoring/Jenkins tarafından okunur.

## Dedup Anahtarı — Kimlik Alanlarını Değiştirme

`AlarmPayload.dedup_key()` şu alanların sha256'sıdır: `alarm_name, cluster, namespace, node, pod, service, tags`. Bu anahtar SQLite'ta state satırını adresler.

**Bu alanlardan birini bir check'in payload'ında değiştirirsen**, o alarm yeni bir kimlik kazanır: eski PROBLEM "recovery" üretmeden ölür, yeni satır sıfırdan "açılır". Mesaj/severity/evidence değişebilir ama kimlik alanlarına dokunma.

## Bildirim Kararı (`engine.py:_should_notify`)

```
prev yok            → status != OK ise gönder
status değişti:
   X → OK (recovery) → recovery_notify kapalıysa gönderme
                       last_change'ten recovery_cooldown_sec geçmediyse gönderme
                       aksi halde gönder
   * → PROBLEM/ERROR → gönder
status aynı:
   OK               → gönderme
   PROBLEM/ERROR    → (now - last_sent) >= interval ise gönder
```

`interval` önceliği: `result.repeat_interval_override` → ERROR ise `error_repeat_interval_sec` → `repeat_interval_sec`.

`CheckResult.repeat_interval_override` anlamı: `None`=policy kullan, `0`=hemen tekrar gönder, `86400`=pratikte sustur. Bir check'in tekrar sıklığını kendi kontrol etmesi için bu alanı set eder.

`DedupPolicy` varsayılanları (`policies/dedup.yaml`): `repeat_interval_sec=600`, `error_repeat_interval_sec=900`, `recovery_notify=True`, `recovery_cooldown_sec=60`.

## Maintenance / Silence (`maintenance.py:active_silence`)

`notify_now=True` olsa bile, `maintenance.silences[]` içinde eşleşen aktif bir pencere varsa bildirim bastırılır:
- Pencere zamanı: `starts_at_utc <= now < ends_at_utc` (UTC)
- Eşleşme: `cluster`, `namespace`, `alarm_name` — boş/`*` = wildcard
- Recovery de varsayılan olarak bastırılır; yalnız `allow_recovery: true` ise geçer

## Config Çözümleme (`config_loader.py`)

`load_config(path)`:
1. `base.yaml` okunur, `includes:` listesi pop edilir
2. Her include sırayla `_deep_merge` edilir, sonra `base.yaml`'ın kendi anahtarları en üste merge edilir
3. Tüm string'lerde `${ENV}` → `os.path.expandvars`

**Merge kuralı kritik:** sadece `checks` listesi *birleştirilir* (concat); diğer tüm anahtarlar (dict'ler derin merge, geri kalan) *üzerine yazılır*. Yani her check dosyası kendi check'lerini ekler ama `runtime`/`dedup` gibi blokları sonraki include ezer.

## Check Plugin Kontratı

Yeni check tipi = `CHECK_REGISTRY`'ye bir satır + bir modül. Modül **mutlaka** `run(params: dict) -> CheckResult` (veya `List[CheckResult]`) export etmeli.

```python
# checks/__init__.py
CHECK_REGISTRY = {
    "ocp_pod_health":       "alarmfw.checks.ocp_pod_health",
    ...
}
```

Engine `params`'a `alarm_name`'i kendisi enjekte eder. Check içindeki exception engine tarafından yakalanır → otomatik `Status.ERROR` / `Severity.CRITICAL` payload üretilir; yani check kodu patlasa bile alarm akışı devam eder. `ocp_pod_health` referans implementasyondur (`${ENV}` expand, token dosyası okuma, PROBLEM/OK payload).

## Notifier Fanout (`notifiers/fanout.py`)

`send_with_fallback(payload, primary, fallback)`: primary listesini sırayla dener, ilki başarılı olursa döner; hepsi patlarsa fallback'i dener; o da olmazsa `RuntimeError`. Notifier tipleri: `zabbix_http`, `smtp_mail`, `file_outbox`.

`runtime.dry_run: true` iken `zabbix_http` ve `smtp_mail` `_DryRunWrapper`'a sarılır (ağ/mail çağrısı yapılmaz, loglanır); **`file_outbox` dry-run'da bile gerçek yazar** — test çıktısını dosyadan doğrulamak için.

## Dizin Haritası

```
src/alarmfw/
├── main.py              CLI + daemon döngüsü + FileLock + sinyal
├── engine.py            run_all, _should_notify, _process_result  ← çekirdek
├── config_loader.py     includes + deep_merge + ${ENV}
├── models.py            Status, Severity, AlarmPayload, CheckResult
├── maintenance.py       active_silence (silence pencereleri)
├── checks/              CHECK_REGISTRY + check modülleri (run(params))
├── dedup/
│   ├── policy.py        DedupPolicy
│   └── store_sqlite.py  SqliteStateStore (alarm_state tablosu)
├── notifiers/           fanout + zabbix_http / smtp_mail / file_outbox
└── utils/               time, logging, locking
```

`config/` çalışma zamanı yapılandırması (notifiers/, policies/, checks/, generated/, observe.yaml). `generated/` ve `observe.yaml` gitignore — `alarmfw-api` tarafından üretilir.

## Çalıştırma & Test

```bash
python -m alarmfw run --config config/base.yaml   # interval_sec=0 → tek sefer
python -m pytest tests/
```

State ve davranışı bozmadan denemek için `runtime.dry_run: true` ver; `file_outbox` notifier'ı ile çıktıyı dosyadan oku.

## Bu Repo'nun Komşuları

`alarmfw-api` config'i (`generated/ocp_pod_health.yaml`, `observe.yaml`) üretir ve `alarmfw:latest` image'ını `docker run` ile tetikler. `alarmfw-api`/`alarmfw-ui` SQLite `alarm_state` tablosunu okur. SQLite şemasını (`dedup/store_sqlite.py`) değiştirirken bu okuyucuları unutma.
