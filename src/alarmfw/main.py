import argparse
import logging
import signal
import sys
import time

from alarmfw.config_loader import load_config
from alarmfw.engine import run_all
from alarmfw.utils.logging import setup_logging
from alarmfw.utils.locking import FileLock

log = logging.getLogger("alarmfw")

_shutdown = False


def _handle_signal(sig, frame):
    global _shutdown
    log.info("Signal %s received, shutting down after current cycle...", sig)
    _shutdown = True


def main() -> None:
    p = argparse.ArgumentParser(prog="alarmfw")
    sub = p.add_subparsers(dest="cmd", required=True)

    runp = sub.add_parser("run", help="Run checks (once or as daemon)")
    runp.add_argument("--config", required=True, help="Path to base YAML config")

    args = p.parse_args()

    cfg = load_config(args.config)
    runtime = cfg.get("runtime", {}) or {}
    setup_logging(runtime.get("log_level", "INFO"))

    interval_sec = int(runtime.get("interval_sec", 0))
    daemon_mode = interval_sec > 0

    lock_path = runtime.get("lock_file", "/state/alarmfw.lock")
    lock = FileLock(lock_path)
    try:
        lock.acquire()
    except Exception:
        log.error("Another instance is running (lock: %s)", lock_path)
        sys.exit(3)

    if daemon_mode:
        signal.signal(signal.SIGTERM, _handle_signal)
        signal.signal(signal.SIGINT, _handle_signal)
        log.info("Daemon mode: interval=%ds", interval_sec)

    try:
        last_code = 0
        while True:
            log.info("--- cycle start ---")
            try:
                last_code = run_all(cfg)
            except Exception as e:
                log.error("run_all crashed: %s", e)
                last_code = 2

            if not daemon_mode or _shutdown:
                break

            log.info("--- cycle done (exit_code=%d), next in %ds ---", last_code, interval_sec)
            # interval boyunca uyur ama SIGTERM'e hızlı cevap ver
            for _ in range(interval_sec):
                if _shutdown:
                    break
                time.sleep(1)

        log.info("Exiting (code=%d)", last_code)
        sys.exit(last_code)
    finally:
        lock.release()
