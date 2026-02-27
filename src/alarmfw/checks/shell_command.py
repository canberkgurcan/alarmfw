import json
import subprocess
from typing import Any, Dict
from alarmfw.models import AlarmPayload, CheckResult, Status, Severity
from alarmfw.utils.time import utc_now_iso

def run(params: Dict[str, Any]) -> CheckResult:
    cmd = params["command"]
    timeout = int(params.get("timeout_sec", 30))

    p = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=timeout)
    out = (p.stdout or "").strip()
    err = (p.stderr or "").strip()

    payload_dict: Dict[str, Any] = {}
    if out.startswith("{") and out.endswith("}"):
        try:
            payload_dict = json.loads(out)
        except Exception:
            payload_dict = {}

    status = Status.OK if p.returncode == 0 else Status.PROBLEM
    sev_default = Severity(params.get("severity", "HIGH"))

    message = payload_dict.get("message") or (out if out else err) or f"shell command exit={p.returncode}"

    payload = AlarmPayload(
        alarm_name=params.get("alarm_name", "shell_command"),
        status=Status(payload_dict.get("status", status.value)),
        severity=Severity(payload_dict.get("severity", sev_default.value)),
        message=message,
        timestamp_utc=payload_dict.get("timestamp_utc", utc_now_iso()),
        cluster=payload_dict.get("cluster"),
        namespace=payload_dict.get("namespace"),
        node=payload_dict.get("node"),
        pod=payload_dict.get("pod"),
        service=payload_dict.get("service"),
        tags=payload_dict.get("tags") or {"type": "shell_command"},
        evidence=payload_dict.get("evidence") or {"returncode": p.returncode},
    )
    return CheckResult(payload=payload)
