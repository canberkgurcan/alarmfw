from __future__ import annotations

import logging
from datetime import datetime, timezone, timedelta
from typing import Any, Dict

import requests

log = logging.getLogger("alarmfw.notifier.italarm")

_DEFAULT_TEMPLATE = (
    "[OCP POD HEALTH] Namespace={namespace} Cluster={cluster}"
    " - Pod sağlığını kontrol ediniz."
)

_TR = timezone(timedelta(hours=3))


class ITAlarmWebhookNotifier:
    """
    Sends alarm events to an ITAlarm-style HTTP webhook.

    Config keys:
      type: "italarm_webhook"
      url: "https://italarm.example.com/webhook"
      timeout_sec: 10          (default: 10)
      tablename: "italarm"     (default: "italarm")
      description_template: "[OCP POD HEALTH] Namespace={namespace} Cluster={cluster} - Pod sağlığını kontrol ediniz."
      auth:
        type: "bearer"
        token: "${ITALARM_TOKEN}"

    Template placeholders (filled from payload tags + top-level fields):
      {namespace}, {cluster}, {node}, {department},
      {alertgroup}, {alertkey}, {severity_num}, {alarm_name}, {message}
    """

    def __init__(self, cfg: Dict[str, Any]):
        self.url = cfg["url"]
        self.timeout = int(cfg.get("timeout_sec", 10))
        self.tablename = str(cfg.get("tablename", "italarm"))
        self.description_template = str(cfg.get("description_template", _DEFAULT_TEMPLATE))

        self.headers: Dict[str, str] = {"Content-Type": "application/json"}
        auth = cfg.get("auth") or {}
        if auth.get("type") == "bearer" and auth.get("token"):
            self.headers["Authorization"] = f"Bearer {auth['token']}"

    def _format_date(self, ts_utc: str) -> str:
        """UTC ISO → Turkey time (UTC+3), dd-mm-yyyy HH:MM:SS"""
        try:
            dt = datetime.fromisoformat(ts_utc.replace("Z", "+00:00"))
            return dt.astimezone(_TR).strftime("%d-%m-%Y %H:%M:%S")
        except Exception:
            return datetime.now(_TR).strftime("%d-%m-%Y %H:%M:%S")

    def send(self, payload: Dict[str, Any]) -> None:
        tags = payload.get("tags") or {}
        status = payload.get("status", "")

        ctx = {
            "namespace":    tags.get("namespace") or payload.get("namespace", ""),
            "cluster":      tags.get("cluster")   or payload.get("cluster", ""),
            "node":         tags.get("node", ""),
            "department":   tags.get("department", ""),
            "alertgroup":   tags.get("alertgroup", ""),
            "alertkey":     tags.get("alertkey", "OCP_POD_HEALTH"),
            "severity_num": tags.get("severity_num", "5"),
            "alarm_name":   payload.get("alarm_name", ""),
            "message":      payload.get("message", ""),
        }

        try:
            description = self.description_template.format(**ctx)
        except KeyError:
            description = self.description_template

        body = {
            "type":           "1" if status == "PROBLEM" else "0",
            "severity":       ctx["severity_num"],
            "alertgroup":     ctx["alertgroup"],
            "alertkey":       ctx["alertkey"],
            "description":    description,
            "node":           ctx["node"],
            "department":     ctx["department"],
            "occurrencedate": self._format_date(payload.get("timestamp_utc", "")),
            "tablename":      self.tablename,
        }

        r = requests.post(self.url, json=body, headers=self.headers, timeout=self.timeout)
        if r.status_code >= 400:
            raise RuntimeError(f"ITAlarm POST failed: {r.status_code} {r.text[:300]}")
        log.info("ITAlarm notified status=%s http=%s", status, r.status_code)
