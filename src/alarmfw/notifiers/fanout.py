from __future__ import annotations
import logging
from typing import Any, Dict, List

from alarmfw.notifiers.zabbix_http import ZabbixHttpNotifier
from alarmfw.notifiers.smtp_mail import SmtpMailNotifier
from alarmfw.notifiers.file_outbox import FileOutboxNotifier

log = logging.getLogger("alarmfw.notifier.fanout")


class _DryRunWrapper:
    def __init__(self, name: str, inner: Any):
        self.name = name
        self.inner = inner

    def send(self, payload: Dict[str, Any]) -> None:
        # Do not actually call network/email in dry-run
        log.info("[DRY-RUN] notifier=%s would send payload: %s", self.name, payload)


class NotifierFanout:
    def __init__(self, cfg: Dict[str, Any]):
        self.notifiers_cfg = (cfg.get("notifiers") or {})
        runtime = (cfg.get("runtime") or {})
        self.dry_run = bool(runtime.get("dry_run", False))
        self._instances: Dict[str, Any] = {}

    def _get(self, name: str):
        if name in self._instances:
            return self._instances[name]

        ncfg = self.notifiers_cfg.get(name)
        if not ncfg:
            raise KeyError(f"Notifier '{name}' not found in config")

        ntype = ncfg.get("type")

        if ntype == "zabbix_http":
            inst = ZabbixHttpNotifier(ncfg)
            # In dry-run, wrap network notifier
            inst = _DryRunWrapper(name, inst) if self.dry_run else inst

        elif ntype == "smtp_mail":
            inst = SmtpMailNotifier(ncfg)
            # In dry-run, wrap mail notifier
            inst = _DryRunWrapper(name, inst) if self.dry_run else inst

        elif ntype == "file_outbox":
            # file_outbox should ALWAYS work, even in dry-run
            inst = FileOutboxNotifier(ncfg)

        else:
            raise ValueError(f"Unknown notifier type: {ntype}")

        self._instances[name] = inst
        return inst

    def send_with_fallback(self, payload: Dict[str, Any], primary: List[str], fallback: List[str]) -> None:
        last_exc: Exception | None = None

        for n in primary:
            try:
                self._get(n).send(payload)
                return
            except Exception as e:
                last_exc = e
                log.error("Primary notifier '%s' failed: %s", n, e)

        for n in fallback:
            try:
                self._get(n).send(payload)
                return
            except Exception as e:
                last_exc = e
                log.error("Fallback notifier '%s' failed: %s", n, e)

        raise RuntimeError(f"All notifiers failed (last error: {last_exc})")
