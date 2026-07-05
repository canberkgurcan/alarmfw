# Alarm Neden Gitti / Gitmedi?

**Kullanım:** `/debug-alarm <beklenen vs gerçekleşen davranış>`

Bir alarmın gönderilip gönderilmemesi tek noktada belirlenir: `engine.py:_process_result` → `_should_notify` → `active_silence` → `fanout`. Sırayla ele.

## 1. Check hangi status üretti?

```bash
python -m alarmfw run --config config/base.yaml   # logları oku: "Running check: <name>"
```

- Check exception attıysa → `Status.ERROR` (engine yakalar). Log'da `Check crashed:` veya `<tip> failed:` ara.
- `OK` üretildiyse zaten bildirim gitmez (recovery hariç).

## 2. `_should_notify` kararı (`engine.py:26`)

| Durum | Karar |
|---|---|
| İlk görülüş (prev yok) | status != OK ise gönder |
| PROBLEM→OK (recovery) | `recovery_notify` kapalı VEYA `recovery_cooldown_sec` dolmadıysa **gönderme** |
| Status değişti (→PROBLEM/ERROR) | gönder |
| Aynı PROBLEM/ERROR | `(now - last_sent) >= interval` değilse **gönderme** |

`interval`: `repeat_interval_override` → ERROR ise `error_repeat_interval_sec` (900) → `repeat_interval_sec` (600).

**"Alarm bir kez geldi sonra sustu"** → normal dedup; tekrar penceresi dolmadı. `policies/dedup.yaml`'a bak.

**"Recovery gelmedi"** → `recovery_cooldown_sec` (problem çok kısa sürdüyse bastırılır) veya `recovery_notify: false`.

## 3. Maintenance bastırması (`maintenance.py`)

`notify_now=True` olsa bile loglarda şunu ara:
```
Notification suppressed by maintenance (id=..., alarm=...)
```
`maintenance.silences[]` içinde `starts_at_utc <= now < ends_at_utc` olan ve `cluster`/`namespace`/`alarm_name` eşleşen pencere var demektir (boş/`*` = wildcard). Recovery'yi de bastırır — `allow_recovery: true` değilse.

## 4. ERROR neden maile düşmedi?

Kasıtlı: `_process_result` içinde `effective_fallback = [] if status == ERROR else fallback`. ERROR yalnız `primary` (Zabbix) dener, SMTP fallback'e gitmez.

## 5. Notifier başarısız mı?

`fanout.send_with_fallback`: primary → fallback sırayla; hepsi patlarsa `RuntimeError` → `_process_result` return 2 (notify_error). Loglarda:
```
Primary notifier 'zabbix' failed: ...
Fallback notifier 'smtp' failed: ...
```
`runtime.dry_run: true` ise zabbix/smtp gerçekten gönderilmez (sarılır), `file_outbox` gönderir — alarmın "gitmediğini" sanmana yol açabilir.

## 6. State'i incele

Durum SQLite `alarm_state` tablosunda (`runtime.state_db`, varsayılan `/state/alarmfw.sqlite`). `dedup_key` başına `last_status`, `last_sent_ts`, `last_change_ts`, `payload_json` tutar. Beklenmedik dedup davranışında bu satıra bak — kimlik alanları değiştiyse anahtar da değişmiştir.
