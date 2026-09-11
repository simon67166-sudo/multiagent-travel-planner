"""
按浏览器 session 存整份 shared_state 快照（SQLite）-- 单进程本地 demo，
写操作靠 RLock 串行化，没有做多进程并发控制。
"""
from contextlib import contextmanager
import json
import os
from pathlib import Path
import sqlite3
from threading import RLock

class SessionStore:
    def __init__(self, path=None):
        self.path = Path(path or os.getenv("TRAVEL_DB_PATH") or
                         Path(__file__).parent / "data" / "sessions.db")
        self._lock = RLock()

    def _connect(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.path, timeout=30)
        connection.execute("CREATE TABLE IF NOT EXISTS sessions (session_id TEXT PRIMARY KEY, state_json TEXT NOT NULL)")
        return connection

    def load(self, session_id):
        with self._lock:
            connection = self._connect()
            try:
                row = connection.execute("SELECT state_json FROM sessions WHERE session_id = ?", (session_id,)).fetchone()
                return json.loads(row[0]) if row else None
            finally:
                connection.close()

    @contextmanager
    def edit(self, session_id, factory):
        # 每次都从库里重新解码出一份新 state，yield 期间抛异常也不会有"改了一半"的脏状态留在内存里
        with self._lock:
            state = self.load(session_id)
            if state is None:
                state = factory()
            yield state
            serialized = json.dumps(state, ensure_ascii=False)
            connection = self._connect()
            try:
                with connection:
                    connection.execute("INSERT INTO sessions VALUES (?, ?) ON CONFLICT(session_id) DO UPDATE SET state_json=excluded.state_json",
                                       (session_id, serialized))
            finally:
                connection.close()
