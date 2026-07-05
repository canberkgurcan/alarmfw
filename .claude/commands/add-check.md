# Yeni Check Tipi Ekle

**Kullanım:** `/add-check <check tipinin ne kontrol edeceğini açıkla>`

Bir check tipi = registry'ye bir satır + `run(params)` export eden bir modül. Engine geri kalanını (dedup, notify, state) halleder.

## 1. Modülü yaz → `checks/<tip>.py`

```python
from __future__ import annotations
from typing import TYPE_CHECKING
if TYPE_CHECKING:
    from alarmfw.models import CheckResult

def run(params: dict) -> "CheckResult":
    from alarmfw.models import CheckResult, AlarmPayload, Status, Severity
    from alarmfw.utils.time import utc_now_iso

    alarm_name = str(params.get("alarm_name", "<tip>"))   # engine enjekte eder
    # ... kontrol mantığı ...

    status = Status.PROBLEM if sorun_var else Status.OK
    payload = AlarmPayload(
        alarm_name=alarm_name,
        status=status,
        severity=Severity.WARN,
        message="...",
        timestamp_utc=utc_now_iso(),
        cluster=params.get("cluster"),     # ← dedup_key kimlik alanları
        namespace=params.get("namespace"),
        tags={"type": "<tip>"},
        evidence={...},
    )
    return CheckResult(payload=payload)
```

**Exception fırlatabilirsin** — engine yakalar ve otomatik `Status.ERROR` payload üretir. Try/except ile kendin yakalarsan da ERROR payload dön; sessizce yutma.

Birden fazla varlık kontrol ediyorsan (ör. her pod için ayrı alarm) `List[CheckResult]` dön.

## 2. Registry'ye ekle → `checks/__init__.py`

```python
CHECK_REGISTRY = {
    ...
    "<tip>": "alarmfw.checks.<tip>",
}
```

## 3. Kimlik alanlarına dikkat

`dedup_key` = `alarm_name + cluster + namespace + node + pod + service + tags`. Aynı mantıksal alarmın her çağrıda **aynı** kimlik alanlarını üretmesi şart — yoksa dedup kopar, recovery üretilmez. `message`/`severity`/`evidence` serbestçe değişebilir.

## 4. Tekrar sıklığını kontrol etmek istersen

`CheckResult(payload=..., repeat_interval_override=N)`:
- `None` → policy (`repeat_interval_sec` / `error_repeat_interval_sec`)
- `0` → her cycle gönder
- `86400` → pratikte sustur (günde bir)

## 5. Config + test

`config/checks/<tip>_example.yaml` ile örnek bir check tanımla, `base.yaml` includes'a ekle, sonra:

```bash
python -m alarmfw run --config config/base.yaml   # runtime.dry_run: true + file_outbox ile güvenli dene
python -m pytest tests/
```

Test pattern: `run(params)` fonksiyonunu doğrudan çağır, dönen `CheckResult.payload.status`'u doğrula — TestClient/daemon kurma.
