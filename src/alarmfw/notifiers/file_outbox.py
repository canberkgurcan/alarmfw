"""FileOutboxNotifier — alarm payload'larını JSON dosyası olarak dizine yazar.

Config örneği:
  notifiers:
    dev_outbox:
      type: "file_outbox"
      directory: "/state/outbox"   # varsayılan: /tmp/alarmfw_outbox
"""
from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict

log = logging.getLogger("alarmfw.notifier.file_outbox")


class FileOutboxNotifier:
    def __init__(self, cfg: Dict[str, Any]):
        self.directory = Path(cfg.get("directory", "/tmp/alarmfw_outbox"))
        self.directory.mkdir(parents=True, exist_ok=True)

    def send(self, payload: Dict[str, Any]) -> None:
        ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%f")
        alarm = payload.get("alarm_name", "alarm").replace("/", "_")
        fname = self.directory / f"{ts}_{alarm}.json"
        fname.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        log.info("FileOutbox: alarm yazıldı → %s", fname)
