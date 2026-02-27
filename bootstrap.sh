#!/usr/bin/env bash
set -euo pipefail

# Folders
mkdir -p src/alarmfw/{checks,notifiers,dedup,utils}
mkdir -p config/{checks,notifiers,policies,examples}
mkdir -p state

# pyproject.toml
cat > pyproject.toml <<'TOML'
[project]
name = "alarmfw"
version = "0.1.0"
description = "Alarm framework (docker + yaml + sqlite dedup + zabbix + smtp fallback)"
requires-python = ">=3.11"
dependencies = [
  "PyYAML>=6.0.1",
  "requests>=2.32.0",
]

[project.scripts]
alarmfw = "alarmfw.main:main"

[tool.setuptools]
package-dir = {"" = "src"}

[tool.setuptools.packages.find]
where = ["src"]
TOML

# Dockerfile
cat > Dockerfile <<'DOCKER'
FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
      ca-certificates \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml /app/pyproject.toml
RUN pip install --no-cache-dir -U pip setuptools wheel \
 && pip install --no-cache-dir .

COPY src/ /app/src/
RUN pip install --no-cache-dir -e .

RUN mkdir -p /config /state

ENTRYPOINT ["alarmfw"]
CMD ["run", "--config", "/config/base.yaml"]
DOCKER

# docker-compose.yml
cat > docker-compose.yml <<'YAML'
services:
  alarmfw:
    build: .
    image: alarmfw:latest
    environment:
      - ZABBIX_URL=${ZABBIX_URL}
      - ZABBIX_TOKEN=${ZABBIX_TOKEN}
      - SMTP_HOST=${SMTP_HOST}
      - SMTP_PORT=${SMTP_PORT}
      - SMTP_USER=${SMTP_USER}
      - SMTP_PASS=${SMTP_PASS}
      - SMTP_TO=${SMTP_TO}
    volumes:
      - ./config:/config:ro
      - ./state:/state
    command: ["run", "--config", "/config/base.yaml"]
YAML

# README.md
cat > README.md <<'MD'
# alarmfw

## Build
docker build -t alarmfw:latest .

## Run (compose)
cp config/examples/minimal.env .env
mkdir -p state
docker compose up --build --abort-on-container-exit

## Run (plain docker)
docker run --rm \
  -e ZABBIX_URL -e ZABBIX_TOKEN \
  -e SMTP_HOST -e SMTP_PORT -e SMTP_USER -e SMTP_PASS -e SMTP_TO \
  -v "$PWD/config:/config:ro" \
  -v "$PWD/state:/state" \
  alarmfw:latest run --config /config/base.yaml
MD

# src/alarmfw/__init__.py
cat > src/alarmfw/__init__.py <<'PY'
__all__ = []
PY

# utils
cat > src/alarmfw/utils/__init__.py <<'PY'
__all__ = []
PY

cat > src/alarmfw/utils/time.py <<'PY'
from datetime import datetime, timezone

def utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
PY

cat > src/alarmfw/utils/logging.py <<'PY'
import logging
import sys

def setup_logging(level: str = "INFO") -> None:
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)sZ %(levelname)s %(name)s - %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S",
        stream=sys.stdout,
    )
PY

cat > src/alarmfw/utils/locking.py <<'PY'
import os

class FileLock:
    def __init__(self, path: str):
        self.path = path
        self.fd = None

    def acquire(self) -> None:
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        self.fd = os.open(self.path, os.O_CREAT | os.O_RDWR, 0o644)
        try:
            import fcntl
            fcntl.flock(self.fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except Exception:
            os.close(self.fd)
            self.fd = None
            raise

    def release(self) -> None:
        if self.fd is None:
            return
        try:
            import fcntl
            fcntl.flock(self.fd, fcntl.LOCK_UN)
        finally:
            os.close(self.fd)
            self.fd = None
PY

# models.py
cat > src/alarmfw/models.py <<'PY'
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
PY

# config_loader.py
cat > src/alarmfw/config_loader.py <<'PY'
import os
from typing import Any, Dict, List
import yaml

def _expand_env(value: Any) -> Any:
    if isinstance(value, str):
        return os.path.expandvars(value)
    if isinstance(value, list):
        return [_expand_env(v) for v in value]
    if isinstance(value, dict):
        return {k: _expand_env(v) for k, v in value.items()}
    return value

def _deep_merge(a: Dict[str, Any], b: Dict[str, Any]) -> Dict[str, Any]:
    out = dict(a)
    for k, v in b.items():
        if k in out and isinstance(out[k], dict) and isinstance(v, dict):
            out[k] = _deep_merge(out[k], v)
        elif k in out and isinstance(out[k], list) and isinstance(v, list) and k in ("checks",):
            out[k] = out[k] + v
        else:
            out[k] = v
    return out

def load_config(path: str) -> Dict[str, Any]:
    base_dir = os.path.dirname(os.path.abspath(path))
    with open(path, "r", encoding="utf-8") as f:
        cfg = yaml.safe_load(f) or {}

    includes: List[str] = cfg.pop("includes", []) or []
    merged: Dict[str, Any] = {}

    for inc in includes:
        inc_path = inc if os.path.isabs(inc) else os.path.join(base_dir, inc)
        with open(inc_path, "r", encoding="utf-8") as f:
            inc_cfg = yaml.safe_load(f) or {}
        merged = _deep_merge(merged, inc_cfg)

    merged = _deep_merge(merged, cfg)
    return _expand_env(merged)
PY

# dedup
cat > src/alarmfw/dedup/__init__.py <<'PY'
__all__ = []
PY

cat > src/alarmfw/dedup/policy.py <<'PY'
from dataclasses import dataclass

@dataclass(frozen=True)
class DedupPolicy:
    repeat_interval_sec: int = 600
    recovery_notify: bool = True
    recovery_cooldown_sec: int = 60
    error_repeat_interval_sec: int = 900

    @staticmethod
    def from_config(cfg: dict) -> "DedupPolicy":
        d = (cfg or {}).get("dedup", {}) or {}
        return DedupPolicy(
            repeat_interval_sec=int(d.get("repeat_interval_sec", 600)),
            recovery_notify=bool(d.get("recovery_notify", True)),
            recovery_cooldown_sec=int(d.get("recovery_cooldown_sec", 60)),
            error_repeat_interval_sec=int(d.get("error_repeat_interval_sec", 900)),
        )
PY

cat > src/alarmfw/dedup/store_sqlite.py <<'PY'
import os
import sqlite3
import time
from typing import Optional, Tuple
from alarmfw.models import Status

class SqliteStateStore:
    def __init__(self, db_path: str):
        self.db_path = db_path
        os.makedirs(os.path.dirname(db_path), exist_ok=True)
        self._init()

    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=10)
        conn.execute("PRAGMA journal_mode=WAL;")
        return conn

    def _init(self) -> None:
        with self._conn() as c:
            c.execute(
                """
                CREATE TABLE IF NOT EXISTS alarm_state (
                  dedup_key TEXT PRIMARY KEY,
                  last_status TEXT NOT NULL,
                  last_sent_ts INTEGER,
                  last_change_ts INTEGER NOT NULL
                )
                """
            )

    def get(self, dedup_key: str) -> Optional[Tuple[str, Optional[int], int]]:
        with self._conn() as c:
            cur = c.execute(
                "SELECT last_status, last_sent_ts, last_change_ts FROM alarm_state WHERE dedup_key=?",
                (dedup_key,),
            )
            row = cur.fetchone()
            return (row[0], row[1], row[2]) if row else None

    def upsert(self, dedup_key: str, last_status: Status, last_sent_ts: Optional[int], last_change_ts: int) -> None:
        with self._conn() as c:
            c.execute(
                """
                INSERT INTO alarm_state(dedup_key,last_status,last_sent_ts,last_change_ts)
                VALUES(?,?,?,?)
                ON CONFLICT(dedup_key) DO UPDATE SET
                  last_status=excluded.last_status,
                  last_sent_ts=excluded.last_sent_ts,
                  last_change_ts=excluded.last_change_ts
                """,
                (dedup_key, last_status.value, last_sent_ts, last_change_ts),
            )

    @staticmethod
    def now_ts() -> int:
        return int(time.time())
PY

# checks
cat > src/alarmfw/checks/__init__.py <<'PY'
CHECK_REGISTRY = {
    "dummy": "alarmfw.checks.dummy",
    "shell_command": "alarmfw.checks.shell_command",
}
PY

cat > src/alarmfw/checks/dummy.py <<'PY'
from typing import Any, Dict
from alarmfw.models import AlarmPayload, CheckResult, Status, Severity
from alarmfw.utils.time import utc_now_iso

def run(params: Dict[str, Any]) -> CheckResult:
    payload = AlarmPayload(
        alarm_name=params.get("alarm_name", "dummy"),
        status=Status.OK,
        severity=Severity.INFO,
        message=params.get("message", "dummy ok"),
        timestamp_utc=utc_now_iso(),
        tags={"type": "dummy"},
    )
    return CheckResult(payload=payload)
PY

cat > src/alarmfw/checks/shell_command.py <<'PY'
import json
import subprocess
from typing import Any, Dict
from alarmfw.models import AlarmPayload, CheckResult, Status, Severity
from alarmfw.utils.time import utc_now_iso

def run(params: Dict[str, Any]) -> CheckResult:
    cmd = params["command"]
    timeout = int(params.get("timeout_sec", 30))

    p = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=timeout)
    out = (p.stdout or "").strip()
    err = (p.stderr or "").strip()

    payload_dict: Dict[str, Any] = {}
    if out.startswith("{") and out.endswith("}"):
        try:
            payload_dict = json.loads(out)
        except Exception:
            payload_dict = {}

    status = Status.OK if p.returncode == 0 else Status.PROBLEM
    sev_default = Severity(params.get("severity", "HIGH"))

    message = payload_dict.get("message") or (out if out else err) or f"shell command exit={p.returncode}"

    payload = AlarmPayload(
        alarm_name=params.get("alarm_name", "shell_command"),
        status=Status(payload_dict.get("status", status.value)),
        severity=Severity(payload_dict.get("severity", sev_default.value)),
        message=message,
        timestamp_utc=payload_dict.get("timestamp_utc", utc_now_iso()),
        cluster=payload_dict.get("cluster"),
        namespace=payload_dict.get("namespace"),
        node=payload_dict.get("node"),
        pod=payload_dict.get("pod"),
        service=payload_dict.get("service"),
        tags=payload_dict.get("tags") or {"type": "shell_command"},
        evidence=payload_dict.get("evidence") or {"returncode": p.returncode},
    )
    return CheckResult(payload=payload)
PY

# notifiers
cat > src/alarmfw/notifiers/__init__.py <<'PY'
__all__ = []
PY

cat > src/alarmfw/notifiers/zabbix_http.py <<'PY'
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
PY

cat > src/alarmfw/notifiers/smtp_mail.py <<'PY'
import logging
import smtplib
from email.message import EmailMessage
from typing import Any, Dict, List

log = logging.getLogger("alarmfw.notifier.smtp")

class SmtpMailNotifier:
    def __init__(self, cfg: Dict[str, Any]):
        self.host = cfg["host"]
        self.port = int(cfg.get("port", 587))
        self.user = cfg.get("user")
        self.password = cfg.get("password")
        self.use_tls = bool(cfg.get("use_tls", True))
        self.mail_from = cfg.get("from", self.user or "alarmfw@localhost")
        self.to: List[str] = list(cfg.get("to", []) or [])
        self.subject_prefix = cfg.get("subject_prefix", "[ALARMFW]")

    def send(self, payload: Dict[str, Any]) -> None:
        subject = f"{self.subject_prefix}[{payload.get('severity')}][{payload.get('status')}] {payload.get('alarm_name')}"
        msg = EmailMessage()
        msg["From"] = self.mail_from
        msg["To"] = ", ".join(self.to)
        msg["Subject"] = subject

        lines = []
        for k in ("timestamp_utc", "alarm_name", "status", "severity", "message", "cluster", "namespace", "node", "pod", "service"):
            if payload.get(k) is not None:
                lines.append(f"{k}: {payload.get(k)}")
        lines.append("")
        lines.append("tags:")
        for k, v in (payload.get("tags") or {}).items():
            lines.append(f"  {k}: {v}")
        lines.append("")
        lines.append("evidence:")
        for k, v in (payload.get("evidence") or {}).items():
            lines.append(f"  {k}: {v}")

        msg.set_content("\n".join(lines))

        with smtplib.SMTP(self.host, self.port, timeout=10) as s:
            if self.use_tls:
                s.starttls()
            if self.user and self.password:
                s.login(self.user, self.password)
            s.send_message(msg)
        log.info("SMTP mail sent")
PY

cat > src/alarmfw/notifiers/fanout.py <<'PY'
import logging
from typing import Any, Dict, List
from alarmfw.notifiers.zabbix_http import ZabbixHttpNotifier
from alarmfw.notifiers.smtp_mail import SmtpMailNotifier

log = logging.getLogger("alarmfw.notifier.fanout")

class NotifierFanout:
    def __init__(self, cfg: Dict[str, Any]):
        self.notifiers_cfg = (cfg.get("notifiers") or {})
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
        elif ntype == "smtp_mail":
            inst = SmtpMailNotifier(ncfg)
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
PY

# engine.py
cat > src/alarmfw/engine.py <<'PY'
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
PY

# main.py
cat > src/alarmfw/main.py <<'PY'
import argparse
import logging
import sys

from alarmfw.config_loader import load_config
from alarmfw.engine import run_all
from alarmfw.utils.logging import setup_logging
from alarmfw.utils.locking import FileLock

log = logging.getLogger("alarmfw")

def main() -> None:
    p = argparse.ArgumentParser(prog="alarmfw")
    sub = p.add_subparsers(dest="cmd", required=True)

    runp = sub.add_parser("run", help="Run all configured checks once")
    runp.add_argument("--config", required=True, help="Path to base YAML config")

    args = p.parse_args()

    cfg = load_config(args.config)
    runtime = cfg.get("runtime", {}) or {}
    setup_logging(runtime.get("log_level", "INFO"))

    lock_path = runtime.get("lock_file", "/state/alarmfw.lock")
    lock = FileLock(lock_path)
    try:
        lock.acquire()
    except Exception:
        log.error("Another instance is running (lock: %s)", lock_path)
        sys.exit(3)

    try:
        code = run_all(cfg)
        sys.exit(code)
    finally:
        lock.release()
PY

# YAML config set
cat > config/base.yaml <<'YAML'
includes:
  - notifiers/zabbix.yaml
  - notifiers/smtp.yaml
  - policies/dedup.yaml
  - checks/dummy.yaml
  - checks/shell_example.yaml

runtime:
  lock_file: "/state/alarmfw.lock"
  state_db: "/state/alarmfw.sqlite"
  log_level: "INFO"
YAML

cat > config/notifiers/zabbix.yaml <<'YAML'
notifiers:
  zabbix:
    type: "zabbix_http"
    url: "${ZABBIX_URL}"
    timeout_sec: 10
    headers:
      Content-Type: "application/json"
    auth:
      type: "bearer"
      token: "${ZABBIX_TOKEN}"
YAML

cat > config/notifiers/smtp.yaml <<'YAML'
notifiers:
  smtp:
    type: "smtp_mail"
    host: "${SMTP_HOST}"
    port: "${SMTP_PORT}"
    user: "${SMTP_USER}"
    password: "${SMTP_PASS}"
    from: "${SMTP_USER}"
    to: ["${SMTP_TO}"]
    subject_prefix: "[ALARMFW]"
    use_tls: true
YAML

cat > config/policies/dedup.yaml <<'YAML'
dedup:
  repeat_interval_sec: 600
  recovery_notify: true
  recovery_cooldown_sec: 60
  error_repeat_interval_sec: 900
YAML

cat > config/checks/dummy.yaml <<'YAML'
checks:
  - name: "dummy_ok"
    type: "dummy"
    enabled: true
    params:
      message: "dummy always OK"
    notify:
      primary: ["zabbix"]
      fallback: ["smtp"]
YAML

cat > config/checks/shell_example.yaml <<'YAML'
checks:
  - name: "shell_echo"
    type: "shell_command"
    enabled: true
    params:
      command: "echo hello-from-shell"
      timeout_sec: 10
      severity: "WARN"
    notify:
      primary: ["zabbix"]
      fallback: ["smtp"]
YAML

cat > config/examples/minimal.env <<'EOFENV'
ZABBIX_URL=https://zabbix.example.com/api/webhook
ZABBIX_TOKEN=REDACTED

SMTP_HOST=smtp.example.com
SMTP_PORT=587
SMTP_USER=alarmfw@example.com
SMTP_PASS=REDACTED
SMTP_TO=noc@example.com
EOFENV

chmod +x bootstrap.sh
echo "OK: files generated. Next: ./bootstrap.sh was created (already executable). Run it: ./bootstrap.sh"
