from __future__ import annotations

import os
import re
import requests
from dataclasses import dataclass
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List

from typing import TYPE_CHECKING
if TYPE_CHECKING:
    from alarmfw.models import CheckResult

@dataclass
class PodIssue:
    pod: str
    ready_ok: bool
    ready_str: str
    phase: str
    waiting: str
    terminated: str
    workload: str
    restarts: int
    node: str
    created_at: str
    image: str


def _workload_from_ownerrefs(owner_refs: Any) -> str:
    try:
        if owner_refs and len(owner_refs) > 0:
            k = owner_refs[0].get("kind") or "unknown"
            n = owner_refs[0].get("name") or "unknown"
            return f"{k}/{n}"
    except Exception:
        pass
    return "unknown/unknown"


def _sum_restarts(container_statuses: Any) -> int:
    total = 0
    if not container_statuses:
        return 0
    for cs in container_statuses:
        try:
            total += int(cs.get("restartCount") or 0)
        except Exception:
            pass
    return total


def _ready_ok(container_statuses: Any) -> bool:
    if not container_statuses:
        return True
    total = len(container_statuses)
    ready = sum(1 for cs in container_statuses if cs.get("ready") is True)
    return total > 0 and ready == total


def _to_gmt3(ts: str) -> str:
    try:
        dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        return dt.astimezone(timezone(timedelta(hours=3))).strftime("%Y-%m-%d %H:%M")
    except Exception:
        return ts


def _image_tag(containers: Any) -> str:
    try:
        image = (containers or [])[0].get("image") or "-"
        return image.split(":")[-1] if ":" in image else image
    except Exception:
        return "-"


def _ready_str(container_statuses: Any) -> str:
    if not container_statuses:
        return "0/0"
    total = len(container_statuses)
    ready = sum(1 for cs in container_statuses if cs.get("ready") is True)
    return f"{ready}/{total}"


def _waiting_reasons(container_statuses: Any) -> str:
    reasons: List[str] = []
    for cs in (container_statuses or []):
        st = cs.get("state") or {}
        w = st.get("waiting")
        if w and w.get("reason"):
            reasons.append(str(w["reason"]))
    out: List[str] = []
    for r in reasons:
        if r not in out:
            out.append(r)
    return ",".join(out)


def _terminated_reasons(container_statuses: Any) -> str:
    reasons: List[str] = []
    for cs in (container_statuses or []):
        st = cs.get("state") or {}
        t = st.get("terminated")
        if t and t.get("reason"):
            reasons.append(str(t["reason"]))
    out: List[str] = []
    for r in reasons:
        if r not in out:
            out.append(r)
    return ",".join(out)


# -------------------------
# ENV EXPANSION (NEW)
# -------------------------
_ENV_PATTERN = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")


def expand_env(value: str) -> str:
    """
    Replace ${VAR} with os.environ['VAR'].
    If VAR is missing, keep it as-is so we can error clearly later.
    """
    if not isinstance(value, str):
        return value

    def repl(m: re.Match) -> str:
        k = m.group(1)
        return os.environ.get(k, m.group(0))

    return _ENV_PATTERN.sub(repl, value)


def _is_problem(issue: PodIssue) -> bool:
    if issue.phase in {"Failed", "Unknown", "Pending"}:
        return True

    waiting = issue.waiting or ""
    terminated = issue.terminated or ""

    bad_wait = (
        "CrashLoopBackOff" in waiting
        or "ImagePullBackOff" in waiting
        or "ErrImagePull" in waiting
        or "CreateContainerConfigError" in waiting
        or "CreateContainerError" in waiting
        or "RunContainerError" in waiting
        or "ContainerCreating" in waiting
        or waiting == "Error"
    )
    if bad_wait:
        return True

    if issue.phase == "Running" and not issue.ready_ok:
        return True

    if "OOMKilled" in terminated:
        return True

    return False


def _get_pods_http(
    api: str,
    token: str,
    insecure: bool,
    namespace: str,
    timeout_sec: int,
) -> Dict[str, Any]:
    resp = requests.get(
        f"{api}/api/v1/namespaces/{namespace}/pods",
        headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
        timeout=timeout_sec,
        verify=not insecure,
    )
    resp.raise_for_status()
    return resp.json()


class OcpPodHealthCheck:
    def __init__(self, params: Dict[str, Any]):
        self.namespace = str(params["namespace"])
        self.cluster = str(params["cluster"])

        # expand ${ENV}
        self.api = expand_env(str(params["ocp_api"])).strip()
        self.token_file = str(params["ocp_token_file"]).strip()

        # validate expansion
        if "${" in self.api or "}" in self.api:
            raise ValueError(f"ocp_api env not expanded: {self.api}")

        self.insecure = str(params.get("ocp_insecure", "true")).lower() == "true"
        self.timeout_sec = int(params.get("timeout_sec", 30))

    def run(self) -> Dict[str, Any]:
        with open(self.token_file, "r", encoding="utf-8") as f:
            token = f.read().strip()
        if not token:
            raise RuntimeError(f"Token dosyası boş: {self.token_file}")

        pods = _get_pods_http(self.api, token, self.insecure, self.namespace, self.timeout_sec)

        issues: List[PodIssue] = []
        for item in (pods.get("items") or []):
            meta = item.get("metadata") or {}
            stat = item.get("status") or {}

            name = meta.get("name") or "-"
            phase = stat.get("phase") or "Unknown"
            node = (item.get("spec") or {}).get("nodeName") or "-"
            created_at = _to_gmt3(meta.get("creationTimestamp") or "")
            containers = (item.get("spec") or {}).get("containers") or []
            image = _image_tag(containers)

            owner_refs = meta.get("ownerReferences") or []
            workload = _workload_from_ownerrefs(owner_refs)

            if phase == "Succeeded":
                continue
            if workload.startswith("Job/"):
                continue

            cs = stat.get("containerStatuses") or []
            issue = PodIssue(
                pod=name,
                ready_ok=_ready_ok(cs),
                ready_str=_ready_str(cs),
                phase=phase,
                waiting=_waiting_reasons(cs) or "-",
                terminated=_terminated_reasons(cs) or "-",
                workload=workload,
                restarts=_sum_restarts(cs),
                node=node,
                created_at=created_at,
                image=image,
            )

            if _is_problem(issue):
                issues.append(issue)

        if issues:
            top = issues[:15]
            header = f"{'NAME':<55} {'READY':<7} {'STATUS':<20} {'RESTARTS':<10} {'CREATED(+3)':<18} {'NODE':<35} {'IMAGE TAG':<30} {'REPLICASET'}"
            lines = [header, "-" * len(header)]
            for i in top:
                rs = i.workload.split("/", 1)[-1] if "/" in i.workload else i.workload
                status = i.waiting if i.waiting != "-" else (i.terminated if i.terminated != "-" else i.phase)
                lines.append(
                    f"{i.pod:<55} {i.ready_str:<7} {status:<20} {i.restarts:<10} {i.created_at:<18} {i.node:<35} {i.image:<30} {rs}"
                )
            msg = (
                f"[OCP POD HEALTH] ns={self.namespace} cluster={self.cluster} problematic_pods={len(issues)}\n"
                + "\n".join(lines)
            )
            return {
                "status": "PROBLEM",
                "message": msg,
                "evidence": {
                    "namespace": self.namespace,
                    "cluster": self.cluster,
                    "count": len(issues),
                    "pods": [i.__dict__ for i in issues],
                },
            }

        return {
            "status": "OK",
            "message": f"[OCP POD HEALTH] ns={self.namespace} cluster={self.cluster} OK",
            "evidence": {"namespace": self.namespace, "cluster": self.cluster},
        }


def run(params: dict) -> "CheckResult":
    """
    Engine expects: run(params) -> CheckResult
    """
    from alarmfw.models import CheckResult, AlarmPayload, Status, Severity
    from alarmfw.utils.time import utc_now_iso

    alarm_name = str(params.get("alarm_name", "ocp_pod_health"))
    namespace = str(params.get("namespace", ""))
    cluster = str(params.get("cluster", ""))

    # Alarm metadata — notifier'lara (italarm vb.) taşınan ortak etiketler
    base_tags = {
        "type":         "ocp_pod_health",
        "namespace":    namespace,
        "cluster":      cluster,
        "node":         str(params.get("node", "")),
        "department":   str(params.get("department", "")),
        "alertgroup":   str(params.get("alertgroup", "")),
        "alertkey":     str(params.get("alertkey", "OCP_POD_HEALTH")),
        "severity_num": str(params.get("severity", "5")),
    }

    try:
        ocp_api = expand_env(str(params.get("ocp_api", ""))).strip()
        if "${" in ocp_api:
            raise ValueError(f"ocp_api env not expanded: {ocp_api}")

        params2 = dict(params)
        params2["ocp_api"] = ocp_api

        chk = OcpPodHealthCheck(params2)
        out = chk.run()
    except Exception as e:
        payload = AlarmPayload(
            alarm_name=alarm_name,
            status=Status.ERROR,
            severity=Severity.CRITICAL,
            message=f"ocp_pod_health failed: {e}",
            timestamp_utc=utc_now_iso(),
            tags=base_tags,
            evidence={"namespace": namespace, "cluster": cluster},
        )
        return CheckResult(payload=payload)

    # out -> AlarmPayload
    status_str = out.get("status", "ERROR")
    msg = out.get("message", "")
    evidence = out.get("evidence", {}) or {}

    status = Status.PROBLEM if status_str == "PROBLEM" else Status.OK
    sev = Severity.WARN
    if out.get("severity") == "CRITICAL":
        sev = Severity.CRITICAL

    payload = AlarmPayload(
        alarm_name=alarm_name,
        status=status,
        severity=sev,
        message=msg,
        timestamp_utc=utc_now_iso(),
        tags=base_tags,
        evidence=evidence,
    )
    return CheckResult(payload=payload)