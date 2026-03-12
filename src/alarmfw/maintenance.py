from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, Optional

from alarmfw.models import AlarmPayload


def _parse_utc(value: Any) -> Optional[datetime]:
    if value is None:
        return None
    raw = str(value).strip()
    if not raw:
        return None
    if raw.endswith("Z"):
        raw = raw[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(raw)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _match(expected: Any, actual: Optional[str]) -> bool:
    if expected in (None, "", "*"):
        return True
    return str(expected).strip() == (actual or "").strip()


def active_silence(
    maintenance_cfg: Dict[str, Any],
    payload: AlarmPayload,
    *,
    is_recovery: bool,
    now_ts: Optional[int] = None,
) -> Optional[Dict[str, Any]]:
    """
    Returns matched silence config if a notification should be suppressed.
    """
    silences = (maintenance_cfg or {}).get("silences") or []
    if not isinstance(silences, list):
        return None

    now = datetime.now(timezone.utc) if now_ts is None else datetime.fromtimestamp(now_ts, tz=timezone.utc)

    for silence in silences:
        if not isinstance(silence, dict):
            continue
        if not silence.get("enabled", True):
            continue

        start = _parse_utc(silence.get("starts_at_utc"))
        end = _parse_utc(silence.get("ends_at_utc"))
        if start is None or end is None:
            continue
        if not (start <= now < end):
            continue

        if not _match(silence.get("cluster"), payload.cluster):
            continue
        if not _match(silence.get("namespace"), payload.namespace):
            continue
        if not _match(silence.get("alarm_name"), payload.alarm_name):
            continue

        # default: maintenance sırasında recovery de susturulur
        if is_recovery and bool(silence.get("allow_recovery", False)):
            continue
        return silence

    return None
