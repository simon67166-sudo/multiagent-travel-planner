"""SQLite full-session snapshots; one local demo process serializes state updates."""
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
        # Fresh decoded state means exceptions never leak partial mutations into memory.
        with self._lock:
            state = self.load(session_id)
            if state is None:
                state = factory()
            yield state
            state.pop("_guardian", None)  # Request-only authority snapshot; never persist stale team data.
            serialized = json.dumps(state, ensure_ascii=False)
            connection = self._connect()
            try:
                with connection:
                    connection.execute("INSERT INTO sessions VALUES (?, ?) ON CONFLICT(session_id) DO UPDATE SET state_json=excluded.state_json",
                                       (session_id, serialized))
            finally:
                connection.close()
