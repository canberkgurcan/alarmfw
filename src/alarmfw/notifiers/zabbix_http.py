import logging
from typing import Any, Dict
import requests

log = logging.getLogger("alarmfw.notifier.zabbix")

class ZabbixHttpNotifier:
    def __init__(self, cfg: Dict[str, Any]):
        self.url = cfg["url"]
        self.timeout = int(cfg.get("timeout_sec", 10))
        self.headers = dict(cfg.get("headers", {}) or {})
        auth = cfg.get("auth", {}) or {}
        if auth.get("type") == "bearer" and auth.get("token"):
            self.headers["Authorization"] = f"Bearer {auth['token']}"

    def send(self, payload: Dict[str, Any]) -> None:
        r = requests.post(self.url, json=payload, headers=self.headers, timeout=self.timeout)
        if r.status_code >= 400:
            raise RuntimeError(f"Zabbix POST failed: {r.status_code} {r.text[:300]}")
        log.info("Zabbix notified (%s)", r.status_code)
