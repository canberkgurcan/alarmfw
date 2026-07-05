# Yeni Notifier Ekle

**Kullanım:** `/add-notifier <hedef sistem: ör. slack, webhook, teams>`

Notifier = `send(payload: Dict[str, Any]) -> None` metodu olan bir sınıf + fanout'ta tanıma + config bloğu.

## 1. Sınıfı yaz → `notifiers/<tip>.py`

```python
from __future__ import annotations
import logging
from typing import Any, Dict

log = logging.getLogger("alarmfw.notifier.<tip>")

class <Tip>Notifier:
    def __init__(self, cfg: Dict[str, Any]):
        self.url = cfg["url"]           # config'ten gelen alanlar
        # ...

    def send(self, payload: Dict[str, Any]) -> None:
        # payload = AlarmPayload.to_dict() (status, severity, message, cluster, ...)
        # Başarısızlıkta exception FIRLAT — fanout fallback'e geçsin diye
        ...
```

`send` başarısızlıkta **exception atmalı**; sessizce yutarsa fanout onu "başarılı" sayar ve fallback devreye girmez.

## 2. Fanout'a tanıt → `notifiers/fanout.py:_get`

```python
elif ntype == "<tip>":
    inst = <Tip>Notifier(ncfg)
    inst = _DryRunWrapper(name, inst) if self.dry_run else inst
```

**Dry-run kararı:** ağa/dışarıya çıkan notifier'lar (`zabbix_http`, `smtp_mail`) dry-run'da sarılır. Sadece `file_outbox` gibi yerel/güvenli olanlar sarılmaz. Yeni notifier dışarı çıkıyorsa `_DryRunWrapper` ile sar.

## 3. Config bloğu → `config/notifiers/<tip>.yaml`

```yaml
notifiers:
  <isim>:
    type: <tip>
    url: "${SLACK_WEBHOOK_URL}"    # ${ENV} expand edilir
```

`base.yaml` `includes:` listesine ekle. Sonra check'lerde kullanılır:
```yaml
notify:
  primary:  [zabbix, <isim>]
  fallback: [smtp]
```

## 4. Davranış notları

- `send_with_fallback` primary'i sırayla dener, **ilk başarılıda durur** — primary'e iki notifier koyarsan ikincisi yalnız ilki patlarsa çalışır.
- ERROR status'lü alarmlar fallback'e gitmez (engine `effective_fallback=[]`). Notifier'ın ERROR alarmlarını da almasını istiyorsan `primary`'e koy.
- `KeyError`/`ValueError` ile config doğrula (bilinmeyen tip → `ValueError`).

## 5. Test

```bash
python -m alarmfw run --config config/base.yaml   # runtime.dry_run: true ile
python -m pytest tests/
```

Gerçek ağ çağrısı yapmadan doğrulamak için `dry_run` aç; `_DryRunWrapper` payload'ı loglar.
