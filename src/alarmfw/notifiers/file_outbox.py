from __future__ import annotations

import json
import os
from typing import Any, Dict
from datetime import datetime, timezone


class FileOutboxNotifier:
    """
    Writes each notification payload to a JSON file under a directory.
    Useful for local testing when Zabbix/SMTP is not reachable.

    Config:
      type: "file_outbox"
      dir: "/state/outbox"
      prefix: "alarmfw"   (optional)
    """

    def __init__(self, cfg: Dict[str, Any]):
        self.dir = cfg.get("dir", "/state/outbox")
        self.prefix = cfg.get("prefix", "alarmfw")
        os.makedirs(self.dir, exist_ok=True)

    def send(self, payload: Dict[str, Any]) -> None:
        ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        alarm_name = str(payload.get("alarm_name", "unknown")).replace("/", "_")
        status = str(payload.get("status", "UNKNOWN"))
        dedup_key = str(payload.get("dedup_key", "nokey"))[:12]

        fname = f"{self.prefix}_{ts}_{alarm_name}_{status}_{dedup_key}.json"
        path = os.path.join(self.dir, fname)

        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2, sort_keys=True)
            f.write("\n")
        os.replace(tmp, path)
