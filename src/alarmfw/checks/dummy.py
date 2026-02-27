from typing import Any, Dict
from alarmfw.models import AlarmPayload, CheckResult, Status, Severity
from alarmfw.utils.time import utc_now_iso

def run(params: Dict[str, Any]) -> CheckResult:
    payload = AlarmPayload(
        alarm_name=params.get("alarm_name", "dummy"),
        status=Status.OK,
        severity=Severity.INFO,
        message=params.get("message", "dummy ok"),
        timestamp_utc=utc_now_iso(),
        tags={"type": "dummy"},
    )
    return CheckResult(payload=payload)
