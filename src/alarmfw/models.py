from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, Optional
import hashlib

class Status(str, Enum):
    OK = "OK"
    PROBLEM = "PROBLEM"
    ERROR = "ERROR"

class Severity(str, Enum):
    INFO = "INFO"
    WARN = "WARN"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"

@dataclass(frozen=True)
class AlarmPayload:
    alarm_name: str
    status: Status
    severity: Severity
    message: str
    timestamp_utc: str

    cluster: Optional[str] = None
    namespace: Optional[str] = None
    node: Optional[str] = None
    pod: Optional[str] = None
    service: Optional[str] = None

    tags: Dict[str, str] = field(default_factory=dict)
    evidence: Dict[str, Any] = field(default_factory=dict)

    def dedup_key(self) -> str:
        base = {
            "alarm_name": self.alarm_name,
            "cluster": self.cluster,
            "namespace": self.namespace,
            "node": self.node,
            "pod": self.pod,
            "service": self.service,
            "tags": self.tags,
        }
        raw = repr(base).encode("utf-8")
        return hashlib.sha256(raw).hexdigest()

    def to_dict(self) -> Dict[str, Any]:
        d = {
            "alarm_name": self.alarm_name,
            "status": self.status.value,
            "severity": self.severity.value,
            "message": self.message,
            "timestamp_utc": self.timestamp_utc,
            "cluster": self.cluster,
            "namespace": self.namespace,
            "node": self.node,
            "pod": self.pod,
            "service": self.service,
            "tags": self.tags,
            "evidence": self.evidence,
            "dedup_key": self.dedup_key(),
        }
        return {k: v for k, v in d.items() if v is not None}

@dataclass(frozen=True)
class CheckResult:
    payload: AlarmPayload
    repeat_interval_override: Optional[int] = None  # None=policy, 0=hemen, 900=15dk, 86400=suppress
