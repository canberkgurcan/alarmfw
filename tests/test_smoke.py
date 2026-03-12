from __future__ import annotations

import copy
import os
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from alarmfw.config_loader import load_config
from alarmfw.engine import run_all
from alarmfw.notifiers.fanout import NotifierFanout


class ConfigLoaderSmokeTest(unittest.TestCase):
    def test_load_config_merges_includes_and_expands_env(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            include_path = root / "include.yaml"
            base_path = root / "base.yaml"

            include_path.write_text(
                """
notifiers:
  outbox:
    type: file_outbox
    dir: ${OUTBOX_DIR}
checks:
  - name: first-check
    type: dummy
runtime:
  log_level: DEBUG
""".strip()
                + "\n",
                encoding="utf-8",
            )

            base_path.write_text(
                """
includes:
  - include.yaml
runtime:
  interval_sec: 30
checks:
  - name: second-check
    type: dummy
""".strip()
                + "\n",
                encoding="utf-8",
            )

            with patch.dict(os.environ, {"OUTBOX_DIR": "/tmp/alarmfw-outbox-test"}, clear=False):
                cfg = load_config(str(base_path))

            self.assertEqual(cfg["runtime"]["log_level"], "DEBUG")
            self.assertEqual(cfg["runtime"]["interval_sec"], 30)
            self.assertEqual(cfg["notifiers"]["outbox"]["dir"], "/tmp/alarmfw-outbox-test")
            self.assertEqual([c["name"] for c in cfg["checks"]], ["first-check", "second-check"])


class EngineSmokeTest(unittest.TestCase):
    def test_run_all_executes_dummy_check_and_persists_state(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            state_db = str(Path(td) / "state.sqlite")
            cfg = {
                "runtime": {"state_db": state_db},
                "dedup": {
                    "repeat_interval_sec": 600,
                    "recovery_notify": True,
                    "error_repeat_interval_sec": 900,
                },
                "notifiers": {
                    "outbox": {"type": "file_outbox", "dir": str(Path(td) / "outbox")},
                },
                "checks": [
                    {
                        "name": "dummy-smoke",
                        "type": "dummy",
                        "params": {"message": "ok"},
                        "notify": {"primary": ["outbox"], "fallback": []},
                    }
                ],
            }

            code = run_all(cfg)
            self.assertEqual(code, 0)

            with sqlite3.connect(state_db) as conn:
                rows = conn.execute("SELECT last_status FROM alarm_state").fetchall()
            self.assertEqual(rows, [("OK",)])

    def test_dedup_suppresses_repeat_problem_and_sends_recovery(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            state_db = str(Path(td) / "state.sqlite")
            cfg_problem = {
                "runtime": {"state_db": state_db},
                "dedup": {
                    "repeat_interval_sec": 3600,
                    "recovery_notify": True,
                    "recovery_cooldown_sec": 60,
                    "error_repeat_interval_sec": 3600,
                },
                "notifiers": {},
                "checks": [
                    {
                        "name": "shell-smoke",
                        "type": "shell_command",
                        "params": {"command": "exit 1", "timeout_sec": 5},
                        "notify": {"primary": ["noop"], "fallback": []},
                    }
                ],
            }

            sent_payloads: list[dict] = []

            def fake_send(_self: NotifierFanout, payload: dict, primary: list[str], fallback: list[str]) -> None:
                sent_payloads.append(payload)

            with patch.object(
                NotifierFanout,
                "send_with_fallback",
                autospec=True,
                side_effect=fake_send,
            ):
                # run_all icinde now_ts iki kez kullaniliyor:
                # 1) _should_notify 2) upsert hesaplari
                # 1. tur: PROBLEM bildirimi
                # 2. tur: tekrar PROBLEM (repeat interval nedeniyle sessiz)
                # 3. tur: cooldown dolduktan sonra OK (recovery) bildirimi
                with patch(
                    "alarmfw.dedup.store_sqlite.time.time",
                    side_effect=[1000, 1000, 1001, 1001, 1065, 1065],
                ):
                    self.assertEqual(run_all(cfg_problem), 1)
                    self.assertEqual(run_all(cfg_problem), 1)

                    cfg_recovery = copy.deepcopy(cfg_problem)
                    cfg_recovery["checks"][0]["params"]["command"] = "exit 0"
                    self.assertEqual(run_all(cfg_recovery), 0)

            self.assertEqual([p["status"] for p in sent_payloads], ["PROBLEM", "OK"])


    def test_recovery_cooldown_suppresses_early_recovery(self) -> None:
        """recovery_cooldown_sec dolmadan OK gelirse bildirim gitmemeli."""
        with tempfile.TemporaryDirectory() as td:
            state_db = str(Path(td) / "state.sqlite")
            base_cfg = {
                "runtime": {"state_db": state_db},
                "dedup": {
                    "repeat_interval_sec": 3600,
                    "recovery_notify": True,
                    "recovery_cooldown_sec": 3600,  # 1 saat — dolmayacak
                    "error_repeat_interval_sec": 3600,
                },
                "notifiers": {},
                "checks": [
                    {
                        "name": "cooldown-smoke",
                        "type": "shell_command",
                        "params": {"command": "exit 1", "timeout_sec": 5},
                        "notify": {"primary": ["noop"], "fallback": []},
                    }
                ],
            }

            sent_payloads: list[dict] = []

            def fake_send(_self: NotifierFanout, payload: dict, primary: list[str], fallback: list[str]) -> None:
                sent_payloads.append(payload)

            with patch.object(NotifierFanout, "send_with_fallback", autospec=True, side_effect=fake_send):
                run_all(base_cfg)  # PROBLEM → bildirim gönderilir

                cfg_recovery = copy.deepcopy(base_cfg)
                cfg_recovery["checks"][0]["params"]["command"] = "exit 0"
                run_all(cfg_recovery)  # OK geldi ama cooldown dolmadı → bildirim GİTMEMELİ

            # Sadece ilk PROBLEM bildirimi gitmiş olmalı
            self.assertEqual([p["status"] for p in sent_payloads], ["PROBLEM"])

    def test_crashed_check_produces_error_payload(self) -> None:
        """Check modülü exception fırlatırsa engine ERROR state'i persist etmeli."""
        with tempfile.TemporaryDirectory() as td:
            state_db = str(Path(td) / "state.sqlite")
            cfg = {
                "runtime": {"state_db": state_db},
                "dedup": {"repeat_interval_sec": 3600, "recovery_notify": False},
                "notifiers": {
                    "outbox": {"type": "file_outbox", "dir": str(Path(td) / "outbox")},
                },
                "checks": [
                    {
                        "name": "crash-smoke",
                        "type": "dummy",
                        "params": {},
                        "notify": {"primary": ["outbox"], "fallback": []},
                    }
                ],
            }

            with patch("alarmfw.checks.dummy.run", side_effect=RuntimeError("simulated crash")):
                code = run_all(cfg)

            self.assertGreaterEqual(code, 1)
            with sqlite3.connect(state_db) as conn:
                rows = conn.execute("SELECT last_status FROM alarm_state").fetchall()
            self.assertEqual(rows, [("ERROR",)])

    def test_disabled_check_is_skipped(self) -> None:
        """`enabled: false` olan check çalıştırılmamalı, state kaydı oluşmamalı."""
        with tempfile.TemporaryDirectory() as td:
            state_db = str(Path(td) / "state.sqlite")
            cfg = {
                "runtime": {"state_db": state_db},
                "dedup": {"repeat_interval_sec": 3600, "recovery_notify": False},
                "notifiers": {},
                "checks": [
                    {
                        "name": "disabled-check",
                        "type": "shell_command",
                        "enabled": False,
                        "params": {"command": "exit 1"},
                        "notify": {"primary": ["noop"], "fallback": []},
                    }
                ],
            }

            code = run_all(cfg)

            self.assertEqual(code, 0)
            with sqlite3.connect(state_db) as conn:
                rows = conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' AND name='alarm_state'"
                ).fetchall()
            # Tablo yoksa ya da kayıt yoksa check atlandı demektir
            if rows:
                count = conn.execute("SELECT COUNT(*) FROM alarm_state").fetchone()[0]
                self.assertEqual(count, 0)

    def test_maintenance_silence_suppresses_problem_notifications(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            state_db = str(Path(td) / "state.sqlite")
            cfg = {
                "runtime": {"state_db": state_db},
                "dedup": {
                    "repeat_interval_sec": 3600,
                    "recovery_notify": True,
                    "recovery_cooldown_sec": 0,
                    "error_repeat_interval_sec": 3600,
                },
                "maintenance": {
                    "silences": [
                        {
                            "id": "deploy-window",
                            "enabled": True,
                            "cluster": "",
                            "namespace": "",
                            "alarm_name": "",
                            "starts_at_utc": "2000-01-01T00:00:00Z",
                            "ends_at_utc": "2999-01-01T00:00:00Z",
                            "allow_recovery": False,
                        }
                    ]
                },
                "notifiers": {},
                "checks": [
                    {
                        "name": "maintenance-smoke",
                        "type": "shell_command",
                        "params": {"command": "exit 1", "timeout_sec": 5},
                        "notify": {"primary": ["noop"], "fallback": []},
                    }
                ],
            }

            sent_payloads: list[dict] = []

            def fake_send(_self: NotifierFanout, payload: dict, primary: list[str], fallback: list[str]) -> None:
                sent_payloads.append(payload)

            with patch.object(NotifierFanout, "send_with_fallback", autospec=True, side_effect=fake_send):
                code = run_all(cfg)

            self.assertEqual(code, 1)
            self.assertEqual(sent_payloads, [])
            with sqlite3.connect(state_db) as conn:
                rows = conn.execute("SELECT last_status FROM alarm_state").fetchall()
            self.assertEqual(rows, [("PROBLEM",)])


if __name__ == "__main__":
    unittest.main(verbosity=2)
