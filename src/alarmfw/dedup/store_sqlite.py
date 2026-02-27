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
