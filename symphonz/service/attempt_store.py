from __future__ import annotations

from pathlib import Path
import sqlite3
import threading


class AttemptStore:
    """Tracks Codex invocation counts per issue state."""

    def __init__(self, path: Path | None = None):
        self.path = path
        self._lock = threading.Lock()
        self._entries: dict[str, dict] = {}
        if self.path is not None:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self._connect() as connection:
                connection.execute(
                    """
                    CREATE TABLE IF NOT EXISTS issue_attempts (
                        issue_id TEXT PRIMARY KEY,
                        state TEXT NOT NULL,
                        count INTEGER NOT NULL CHECK (count >= 0)
                    )
                    """
                )

    def next_attempt(self, issue_id: str, state: str) -> int:
        if self.path is None:
            with self._lock:
                entry = self._memory_entry(issue_id, state)
                return int(entry["count"])
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT state, count FROM issue_attempts WHERE issue_id = ?",
                (issue_id,),
            ).fetchone()
            if row is None or row[0] != state:
                self._upsert(connection, issue_id, state, 0)
                return 0
            return int(row[1])

    def consume(self, issue_id: str, state: str, max_attempts: int) -> int | None:
        if self.path is None:
            with self._lock:
                entry = self._memory_entry(issue_id, state)
                attempt = int(entry["count"])
                if attempt >= max_attempts:
                    return None
                entry["count"] = attempt + 1
                return attempt
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT state, count FROM issue_attempts WHERE issue_id = ?",
                (issue_id,),
            ).fetchone()
            attempt = 0 if row is None or row[0] != state else int(row[1])
            if attempt >= max_attempts:
                return None
            self._upsert(connection, issue_id, state, attempt + 1)
            return attempt

    def clear(self, issue_id: str) -> None:
        if self.path is None:
            with self._lock:
                self._entries.pop(issue_id, None)
            return
        with self._connect() as connection:
            connection.execute("DELETE FROM issue_attempts WHERE issue_id = ?", (issue_id,))

    def _connect(self) -> sqlite3.Connection:
        if self.path is None:
            raise RuntimeError("In-memory attempt stores do not use SQLite")
        return sqlite3.connect(self.path, timeout=30)

    def _memory_entry(self, issue_id: str, state: str) -> dict:
        entry = self._entries.get(issue_id)
        if entry is None or entry["state"] != state:
            entry = {"state": state, "count": 0}
            self._entries[issue_id] = entry
        return entry

    @staticmethod
    def _upsert(connection: sqlite3.Connection, issue_id: str, state: str, count: int) -> None:
        connection.execute(
            """
            INSERT INTO issue_attempts (issue_id, state, count)
            VALUES (?, ?, ?)
            ON CONFLICT(issue_id) DO UPDATE SET state = excluded.state, count = excluded.count
            """,
            (issue_id, state, count),
        )
