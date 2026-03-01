from __future__ import annotations

import json
import os
import sqlite3
import requests
from typing import Any, Dict, List, Optional, Tuple, TYPE_CHECKING

if TYPE_CHECKING:
    from alarmfw.models import CheckResult

from alarmfw.checks.ocp_pod_health import (
    PodIssue,
    _is_problem,
    _workload_from_ownerrefs,
    _sum_restarts,
    _ready_ok,
    _ready_str,
    _to_gmt3,
    _image_tag,
    _waiting_reasons,
    _terminated_reasons,
    expand_env,
)

_STATE_DB = "/state/alarmfw.sqlite"


def _get_ns_pods_http(api: str, token: str, insecure: bool, namespace: str, timeout_sec: int) -> Dict[str, Any]:
    resp = requests.get(
        f"{api}/api/v1/namespaces/{namespace}/pods",
        headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
        timeout=timeout_sec,
        verify=not insecure,
    )
    resp.raise_for_status()
    return resp.json()


def _find_issues(items: List[Any]) -> List[PodIssue]:
    issues: List[PodIssue] = []
    for item in items:
        meta = item.get("metadata") or {}
        stat = item.get("status") or {}

        phase    = stat.get("phase") or "Unknown"
        workload = _workload_from_ownerrefs(meta.get("ownerReferences") or [])

        if phase == "Succeeded":
            continue
        if workload.startswith("Job/"):
            continue

        cs = stat.get("containerStatuses") or []
        issue = PodIssue(
            pod        = meta.get("name") or "-",
            ready_ok   = _ready_ok(cs),
            ready_str  = _ready_str(cs),
            phase      = phase,
            waiting    = _waiting_reasons(cs) or "-",
            terminated = _terminated_reasons(cs) or "-",
            workload   = workload,
            restarts   = _sum_restarts(cs),
            node       = (item.get("spec") or {}).get("nodeName") or "-",
            created_at = _to_gmt3(meta.get("creationTimestamp") or ""),
            image      = _image_tag((item.get("spec") or {}).get("containers") or []),
        )
        if _is_problem(issue):
            issues.append(issue)
    return issues


def _build_tags(ns_cfg: Dict[str, Any], cluster: str, ns: str) -> Dict[str, str]:
    return {
        "type":         "ocp_pod_health",
        "namespace":    ns,
        "cluster":      cluster,
        "node":         str(ns_cfg.get("node", "")),
        "department":   str(ns_cfg.get("department", "")),
        "alertgroup":   str(ns_cfg.get("alertgroup", "")),
        "alertkey":     str(ns_cfg.get("alertkey", "OCP_POD_HEALTH")),
        "severity_num": str(ns_cfg.get("severity", "5")),
    }


def _read_prev_payload(alarm_name: str) -> Optional[Dict[str, Any]]:
    """SQLite'tan bir alarm'in son payload_json'ini doner."""
    try:
        if not os.path.exists(_STATE_DB):
            return None
        conn = sqlite3.connect(_STATE_DB, timeout=3)
        row = conn.execute(
            "SELECT payload_json FROM alarm_state WHERE alarm_name=? LIMIT 1",
            (alarm_name,),
        ).fetchone()
        conn.close()
        if row and row[0]:
            return json.loads(row[0])
    except Exception:
        pass
    return None


def _compute_delta(
    issues: List[PodIssue],
    prev_payload: Optional[Dict[str, Any]],
) -> Tuple[List[str], List[str], List[Tuple[str, int, int]], Optional[int]]:
    """
    Onceki payload ile pod listesini karsilastirir.

    Returns:
        new_pods        : yeni eklenen pod isimleri
        recovered_pods  : iyilesen pod isimleri
        restart_up      : [(pod, prev_restarts, curr_restarts), ...]
        repeat_interval_override:
            None  -> engine policy kullan (status degisiyor)
            0     -> hemen bildir (yapisal degisiklik)
            900   -> 15dk cooldown (sadece restart artisi)
            86400 -> bildirim gonderme (hic degisiklik yok)
    """
    # Onceki state yoksa veya status farkli -> engine handle eder
    if prev_payload is None or prev_payload.get("status") != "PROBLEM":
        return [], [], [], None

    # PROBLEM->OK gecisi -> engine handle eder
    if not issues:
        return [], [], [], None

    # Her iki taraf da PROBLEM -> pod-level karsilastirma
    current = {i.pod: i for i in issues}
    prev_pods_list = (prev_payload.get("evidence") or {}).get("pods", [])
    prev = {p["pod"]: int(p.get("restarts", 0)) for p in prev_pods_list if "pod" in p}

    new_pods       = [pod for pod in current if pod not in prev]
    recovered_pods = [pod for pod in prev if pod not in current]
    restart_up     = [
        (pod, prev[pod], current[pod].restarts)
        for pod in current
        if pod in prev and current[pod].restarts > prev[pod]
    ]

    if new_pods or recovered_pods:
        override = 0      # Yapisal degisiklik -> hemen bildir
    elif restart_up:
        override = 900    # Sadece restart artisi -> 15dk cooldown
    else:
        override = 86400  # Degisiklik yok -> bildirim gonderme

    return new_pods, recovered_pods, restart_up, override


def _make_result(
    cluster: str,
    ns: str,
    ns_cfg: Dict[str, Any],
    issues: List[PodIssue],
    new_pods: Optional[List[str]] = None,
    recovered_pods: Optional[List[str]] = None,
    restart_up: Optional[List[Tuple[str, int, int]]] = None,
    repeat_interval_override: Optional[int] = None,
) -> "CheckResult":
    from alarmfw.models import CheckResult, AlarmPayload, Status, Severity
    from alarmfw.utils.time import utc_now_iso

    alarm_name = f"ocp_pod_health__{ns}__{cluster}"
    tags = _build_tags(ns_cfg, cluster, ns)

    new_pods       = new_pods or []
    recovered_pods = recovered_pods or []
    restart_up     = restart_up or []

    if issues:
        top = issues[:15]
        header = (
            f"{'NAME':<55} {'READY':<7} {'STATUS':<20} {'RESTARTS':<10}"
            f" {'CREATED(+3)':<18} {'NODE':<35} {'IMAGE TAG':<30} {'REPLICASET'}"
        )
        lines = [header, "-" * len(header)]
        for i in top:
            rs     = i.workload.split("/", 1)[-1] if "/" in i.workload else i.workload
            status = i.waiting if i.waiting != "-" else (i.terminated if i.terminated != "-" else i.phase)
            lines.append(
                f"{i.pod:<55} {i.ready_str:<7} {status:<20} {i.restarts:<10}"
                f" {i.created_at:<18} {i.node:<35} {i.image:<30} {rs}"
            )

        delta_lines = []
        if new_pods:
            delta_lines.append(f"  + Yeni problem podlar : {', '.join(new_pods)}")
        if recovered_pods:
            delta_lines.append(f"  v Iyilesen podlar     : {', '.join(recovered_pods)}")
        if restart_up:
            for pod, was, now_ in restart_up:
                delta_lines.append(f"  ^ Restart artisi      : {pod}  ({was} -> {now_})")

        delta_str = ("\nDEGISIKLIKLER:\n" + "\n".join(delta_lines) + "\n") if delta_lines else ""

        msg = (
            f"[OCP POD HEALTH] ns={ns} cluster={cluster} problematic_pods={len(issues)}\n"
            + delta_str
            + "\nPROBLEM PODLAR:\n"
            + "\n".join(lines)
        )
        payload = AlarmPayload(
            alarm_name    = alarm_name,
            status        = Status.PROBLEM,
            severity      = Severity.WARN,
            message       = msg,
            timestamp_utc = utc_now_iso(),
            tags          = tags,
            evidence      = {
                "namespace": ns,
                "cluster":   cluster,
                "count":     len(issues),
                "pods":      [i.__dict__ for i in issues],
                "delta": {
                    "new_pods":          new_pods,
                    "recovered_pods":    recovered_pods,
                    "restart_increases": [
                        {"pod": pod, "from": was, "to": now_}
                        for pod, was, now_ in restart_up
                    ],
                } if (new_pods or recovered_pods or restart_up) else None,
            },
        )
    else:
        payload = AlarmPayload(
            alarm_name    = alarm_name,
            status        = Status.OK,
            severity      = Severity.WARN,
            message       = f"[OCP POD HEALTH] ns={ns} cluster={cluster} OK",
            timestamp_utc = utc_now_iso(),
            tags          = tags,
            evidence      = {"namespace": ns, "cluster": cluster},
        )

    return CheckResult(payload=payload, repeat_interval_override=repeat_interval_override)


def run(params: dict) -> List["CheckResult"]:
    """
    Token dosyasini bir kez acar, config'deki her NS icin HTTP cagrisi atar.
    Onceki SQLite state ile karsilastirip delta bilgisine gore bildirim karari verir.
    """
    from alarmfw.models import CheckResult, AlarmPayload, Status, Severity
    from alarmfw.utils.time import utc_now_iso

    cluster      = str(params.get("cluster", ""))
    ocp_api      = expand_env(str(params.get("ocp_api", ""))).strip()
    token_file   = str(params.get("ocp_token_file", "")).strip()
    insecure     = str(params.get("ocp_insecure", "true")).lower() == "true"
    timeout_sec  = int(params.get("timeout_sec", 30))
    ns_configs: List[Dict[str, Any]] = list(params.get("namespaces") or [])

    def _ns_error(ns_cfg: Dict[str, Any], msg: str) -> "CheckResult":
        ns = str(ns_cfg.get("namespace", ""))
        return CheckResult(payload=AlarmPayload(
            alarm_name    = f"ocp_pod_health__{ns}__{cluster}",
            status        = Status.ERROR,
            severity      = Severity.CRITICAL,
            message       = f"[OCP POD HEALTH] cluster={cluster} ns={ns}: {msg}",
            timestamp_utc = utc_now_iso(),
            tags          = _build_tags(ns_cfg, cluster, ns),
            evidence      = {"namespace": ns, "cluster": cluster},
        ))

    if "${" in ocp_api:
        return [_ns_error(c, f"ocp_api env not expanded: {ocp_api}") for c in ns_configs]

    # Token dosyasini bir kez oku
    try:
        with open(token_file, "r", encoding="utf-8") as f:
            token = f.read().strip()
        if not token:
            raise RuntimeError(f"Token dosyasi bos: {token_file}")
    except Exception as e:
        return [_ns_error(c, str(e)) for c in ns_configs]

    # Her NS icin: HTTP cagrisi + delta hesap + result
    results: List["CheckResult"] = []
    for ns_cfg in ns_configs:
        ns         = str(ns_cfg.get("namespace", ""))
        alarm_name = f"ocp_pod_health__{ns}__{cluster}"
        try:
            data   = _get_ns_pods_http(ocp_api, token, insecure, ns, timeout_sec)
            issues = _find_issues(data.get("items") or [])

            prev_payload                              = _read_prev_payload(alarm_name)
            new_pods, recovered_pods, r_up, override = _compute_delta(issues, prev_payload)

            results.append(_make_result(cluster, ns, ns_cfg, issues, new_pods, recovered_pods, r_up, override))
        except Exception as e:
            results.append(_ns_error(ns_cfg, str(e)))

    return results
