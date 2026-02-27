import importlib
import logging
from typing import Any, Dict, List, Tuple

from alarmfw.models import CheckResult, Status, AlarmPayload, Severity
from alarmfw.checks import CHECK_REGISTRY
from alarmfw.dedup.store_sqlite import SqliteStateStore
from alarmfw.dedup.policy import DedupPolicy
from alarmfw.notifiers.fanout import NotifierFanout
from alarmfw.utils.time import utc_now_iso


log = logging.getLogger("alarmfw.engine")

def _load_check_runner(check_type: str):
    mod_path = CHECK_REGISTRY.get(check_type)
    if not mod_path:
        raise ValueError(f"Unknown check type '{check_type}'. Known: {sorted(CHECK_REGISTRY.keys())}")
    mod = importlib.import_module(mod_path)
    if not hasattr(mod, "run"):
        raise ValueError(f"Check module {mod_path} has no run(params) function")
    return mod.run

def _should_notify(store: SqliteStateStore, policy: DedupPolicy, result: CheckResult) -> Tuple[bool, bool]:
    payload = result.payload
    key = payload.dedup_key()
    now = store.now_ts()
    prev = store.get(key)

    is_recovery = False

    if prev is None:
        return (payload.status != Status.OK, False)

    prev_status_str, last_sent_ts, _last_change_ts = prev
    prev_status = Status(prev_status_str)

    if prev_status != payload.status:
        if prev_status != Status.OK and payload.status == Status.OK:
            is_recovery = True
            return (policy.recovery_notify, True)
        return (payload.status != Status.OK, False)

    if payload.status == Status.OK:
        return (False, False)

    interval = policy.error_repeat_interval_sec if payload.status == Status.ERROR else policy.repeat_interval_sec
    if last_sent_ts is None:
        return (True, False)
    return ((now - last_sent_ts) >= interval, False)

def run_all(cfg: Dict[str, Any]) -> int:
    runtime = cfg.get("runtime", {}) or {}
    state_db = runtime.get("state_db", "/state/alarmfw.sqlite")

    store = SqliteStateStore(state_db)
    policy = DedupPolicy.from_config(cfg)
    fanout = NotifierFanout(cfg)

    checks: List[Dict[str, Any]] = list(cfg.get("checks", []) or [])
    if not checks:
        log.warning("No checks configured")
        return 0

    exit_code = 0
    for check in checks:
        if not check.get("enabled", True):
            continue

        name = check["name"]
        ctype = check["type"]
        params = check.get("params", {}) or {}
        notify_cfg = check.get("notify", {}) or {}
        primary = list(notify_cfg.get("primary", ["zabbix"]) or [])
        fallback = list(notify_cfg.get("fallback", ["smtp"]) or [])

        log.info("Running check: %s (%s)", name, ctype)

        try:
            runner = _load_check_runner(ctype)
            result: CheckResult = runner({**params, "alarm_name": name})
        except Exception as e:
            payload = AlarmPayload(
                alarm_name=name,
                status=Status.ERROR,
                severity=Severity.CRITICAL,
                message=f"Check crashed: {e}",
                timestamp_utc=utc_now_iso(),
                tags={"type": ctype},
                evidence={},
            )
            result = CheckResult(payload=payload)

        payload = result.payload
        key = payload.dedup_key()
        now = store.now_ts()

        notify_now, _is_recovery = _should_notify(store, policy, result)

        prev = store.get(key)
        last_change_ts = now if (prev is None or prev[0] != payload.status.value) else (prev[2] if prev else now)

        if notify_now:
            try:
                fanout.send_with_fallback(payload.to_dict(), primary=primary, fallback=fallback)
                store.upsert(key, payload.status, now, last_change_ts)
            except Exception as e:
                log.error("Notify failed for %s: %s", name, e)
                store.upsert(key, payload.status, (prev[1] if prev else None), last_change_ts)
                exit_code = 2
        else:
            store.upsert(key, payload.status, (prev[1] if prev else None), last_change_ts)

        if payload.status in (Status.PROBLEM, Status.ERROR):
            exit_code = max(exit_code, 1)

    return exit_code
