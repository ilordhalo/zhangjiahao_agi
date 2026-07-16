from __future__ import annotations

import base64
import binascii
import json
from pathlib import Path
import secrets
import sqlite3
import time

from symphonz.service.event_log import bounded_redacted_data
from symphonz.service.models import RuntimeErrorRecord, RuntimeEvent, runtime_error_from_event


class RuntimeStoreInputError(ValueError):
    """Raised when a runtime-store pagination input is invalid."""


class RuntimeStore:
    """SQLite repository for runtime history used by the dashboard and auth layers."""

    TASK_PAGE_SNAPSHOT_TTL_SECONDS = 300.0
    TASK_PAGE_SNAPSHOT_CLEANUP_BATCH_SIZE = 100
    LOGIN_RESERVATION_TTL_SECONDS = 120.0
    LOGIN_ATTEMPT_CLEANUP_BATCH_SIZE = 100
    LOGIN_GLOBAL_KDF_LIMIT = 5

    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._legacy_report_sync_status = False
        self._initialize()

    def upsert_issue(self, entry: dict) -> None:
        identifier = entry.get("issue_identifier") or entry.get("identifier")
        if not identifier:
            raise ValueError("Runtime issue entries require issue_identifier")
        identifier = str(identifier)
        now = float(entry.get("updated_at") or time.time())

        def write(connection: sqlite3.Connection) -> None:
            existing = connection.execute(
                "SELECT * FROM issue_runs WHERE issue_identifier = ?", (identifier,)
            ).fetchone()
            values = self._issue_values(entry, existing, now)
            if existing is None:
                connection.execute(
                    """
                    INSERT INTO issue_runs (
                        issue_identifier, issue_id, title, linear_state, status, attempt, workspace,
                        started_at, updated_at, completed_at, cancelled_at, codex_process_id,
                        codex_thread_id, codex_turn_id, codex_session_id, branch, commit_hash,
                        review_url, report_url, report_published_at, latest_error_summary, error_count,
                        details_json
                    ) VALUES (
                        :issue_identifier, :issue_id, :title, :linear_state, :status, :attempt, :workspace,
                        :started_at, :updated_at, :completed_at, :cancelled_at, :codex_process_id,
                        :codex_thread_id, :codex_turn_id, :codex_session_id, :branch, :commit_hash,
                        :review_url, :report_url, :report_published_at, :latest_error_summary, :error_count,
                        :details_json
                    )
                    """,
                    values,
                )
            else:
                assignments = ", ".join(f"{name} = :{name}" for name in values if name != "issue_identifier")
                connection.execute(
                    f"UPDATE issue_runs SET {assignments} WHERE issue_identifier = :issue_identifier", values
                )

        self._write(write)

    def record_event(self, event: RuntimeEvent) -> int:
        data = bounded_redacted_data(event.data)
        severity = str(event.data.get("severity") or "info")
        category = str(event.data.get("category") or event.type.split("_", 1)[0])

        def write(connection: sqlite3.Connection) -> int:
            cursor = connection.execute(
                """
                INSERT INTO runtime_events (timestamp, severity, category, type, issue_identifier, message, data_json)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (event.timestamp, severity, category, event.type, event.issue_identifier, event.message, self._dump(data)),
            )
            if event.issue_identifier:
                connection.execute(
                    """
                    INSERT INTO issue_runs (issue_identifier, status, updated_at, details_json)
                    VALUES (?, ?, ?, '{}')
                    ON CONFLICT(issue_identifier) DO UPDATE SET
                        updated_at = MAX(issue_runs.updated_at, excluded.updated_at)
                    """,
                    (event.issue_identifier, "unknown", event.timestamp),
                )
            derived_error = runtime_error_from_event(event)
            if derived_error is not None:
                self._record_error(connection, derived_error)
            return int(cursor.lastrowid)

        return self._write(write)

    def record_error(self, error: RuntimeErrorRecord) -> int:
        return self._write(lambda connection: self._record_error(connection, error))

    def resolve_error(self, error_id: int, *, resolving_event: str | None = None, resolved_at: float | None = None) -> None:
        self._write(
            lambda connection: connection.execute(
                """
                UPDATE runtime_errors
                SET resolved_at = ?, resolving_event = ?
                WHERE id = ?
                """,
                (time.time() if resolved_at is None else resolved_at, resolving_event, error_id),
            )
        )

    def list_tasks(
        self,
        *,
        status: str | None = None,
        query: str | None = None,
        cursor: str | None = None,
        limit: int = 50,
    ) -> dict:
        conditions: list[str] = []
        parameters: list[object] = []
        if status:
            conditions.append("status = ?")
            parameters.append(status)
        if query:
            conditions.append("(issue_identifier LIKE ? OR title LIKE ?)")
            needle = f"%{query}%"
            parameters.extend([needle, needle])
        return self._list_task_snapshot(conditions, parameters, cursor, limit)

    def get_task(self, issue_identifier: str) -> dict | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM issue_runs WHERE issue_identifier = ?", (issue_identifier,)
            ).fetchone()
        return self._task_from_row(row) if row is not None else None

    def list_events(
        self,
        *,
        issue_identifier: str | None = None,
        severity: str | None = None,
        category: str | None = None,
        event_type: str | None = None,
        cursor: str | None = None,
        limit: int = 50,
    ) -> dict:
        conditions: list[str] = []
        parameters: list[object] = []
        for column, value in (("issue_identifier", issue_identifier), ("severity", severity), ("category", category), ("type", event_type)):
            if value:
                conditions.append(f"{column} = ?")
                parameters.append(value)
        return self._list_keyset_rows(
            "runtime_events",
            conditions,
            parameters,
            cursor,
            limit,
            self._event_from_row,
            order_by="timestamp DESC, id DESC",
            cursor_fields=("timestamp", "id"),
            after_clause="timestamp < ? OR (timestamp = ? AND id < ?)",
            after_parameters=lambda values: (values["timestamp"], values["timestamp"], values["id"]),
        )

    def list_errors(
        self,
        *,
        issue_identifier: str | None = None,
        stage: str | None = None,
        error_type: str | None = None,
        resolved: bool | None = None,
        cursor: str | None = None,
        limit: int = 50,
    ) -> dict:
        conditions: list[str] = []
        parameters: list[object] = []
        for column, value in (("issue_identifier", issue_identifier), ("stage", stage), ("error_type", error_type)):
            if value:
                conditions.append(f"{column} = ?")
                parameters.append(value)
        if resolved is True:
            conditions.append("resolved_at IS NOT NULL")
        elif resolved is False:
            conditions.append("resolved_at IS NULL")
        return self._list_keyset_rows(
            "runtime_errors",
            conditions,
            parameters,
            cursor,
            limit,
            self._error_from_row,
            select_columns="*, resolved_at IS NOT NULL AS resolved_bucket",
            order_by="resolved_bucket ASC, timestamp DESC, id DESC",
            cursor_fields=("resolved_bucket", "timestamp", "id"),
            after_clause=(
                "(resolved_at IS NOT NULL) > ? OR ((resolved_at IS NOT NULL) = ? AND "
                "(timestamp < ? OR (timestamp = ? AND id < ?)))"
            ),
            after_parameters=lambda values: (
                values["resolved_bucket"],
                values["resolved_bucket"],
                values["timestamp"],
                values["timestamp"],
                values["id"],
            ),
        )

    def save_report(self, entry: dict) -> None:
        identifier = entry.get("issue_identifier") or entry.get("identifier")
        if not identifier:
            raise ValueError("Report entries require issue_identifier")
        version = int(entry.get("report_version", 1))
        safe_entry = bounded_redacted_data(entry)
        values = {
            "issue_identifier": str(identifier),
            "report_version": version,
            "json_path": entry.get("json_path"),
            "html_path": entry.get("html_path"),
            "url": entry.get("url"),
            "review_metadata_json": self._dump(bounded_redacted_data(entry.get("review_metadata", {}))),
            "linear_comment_id": entry.get("linear_comment_id"),
            "linear_sync_status": entry.get("linear_sync_status") or entry.get("sync_status") or "pending",
            "retry_count": int(entry.get("retry_count", 0)),
            "next_retry_at": entry.get("next_retry_at"),
            "created_at": float(entry.get("created_at") or time.time()),
            "updated_at": float(entry.get("updated_at") or time.time()),
            "details_json": self._dump(safe_entry),
        }
        if self._legacy_report_sync_status:
            values["sync_status"] = values["linear_sync_status"]
            statement = """
                INSERT INTO reports (
                    issue_identifier, report_version, json_path, html_path, url, review_metadata_json,
                    linear_comment_id, sync_status, linear_sync_status, retry_count, next_retry_at, created_at, updated_at,
                    details_json
                ) VALUES (
                    :issue_identifier, :report_version, :json_path, :html_path, :url, :review_metadata_json,
                    :linear_comment_id, :sync_status, :linear_sync_status, :retry_count, :next_retry_at, :created_at,
                    :updated_at, :details_json
                )
                ON CONFLICT(issue_identifier, report_version) DO UPDATE SET
                    json_path = excluded.json_path,
                    html_path = excluded.html_path,
                    url = excluded.url,
                    review_metadata_json = excluded.review_metadata_json,
                    linear_comment_id = excluded.linear_comment_id,
                    sync_status = excluded.sync_status,
                    linear_sync_status = excluded.linear_sync_status,
                    retry_count = excluded.retry_count,
                    next_retry_at = excluded.next_retry_at,
                    updated_at = excluded.updated_at,
                    details_json = excluded.details_json
            """
        else:
            statement = """
                INSERT INTO reports (
                    issue_identifier, report_version, json_path, html_path, url, review_metadata_json,
                    linear_comment_id, linear_sync_status, retry_count, next_retry_at, created_at, updated_at, details_json
                ) VALUES (
                    :issue_identifier, :report_version, :json_path, :html_path, :url, :review_metadata_json,
                    :linear_comment_id, :linear_sync_status, :retry_count, :next_retry_at, :created_at, :updated_at, :details_json
                )
                ON CONFLICT(issue_identifier, report_version) DO UPDATE SET
                    json_path = excluded.json_path,
                    html_path = excluded.html_path,
                    url = excluded.url,
                    review_metadata_json = excluded.review_metadata_json,
                    linear_comment_id = excluded.linear_comment_id,
                    linear_sync_status = excluded.linear_sync_status,
                    retry_count = excluded.retry_count,
                    next_retry_at = excluded.next_retry_at,
                    updated_at = excluded.updated_at,
                    details_json = excluded.details_json
            """
        self._write(lambda connection: connection.execute(statement, values))

    def get_report(self, issue_identifier: str, report_version: int | None = None) -> dict | None:
        query = "SELECT * FROM reports WHERE issue_identifier = ?"
        parameters: tuple[object, ...] = (issue_identifier,)
        if report_version is not None:
            query += " AND report_version = ?"
            parameters += (report_version,)
        query += " ORDER BY report_version DESC LIMIT 1"
        with self._connect() as connection:
            row = connection.execute(query, parameters).fetchone()
        return self._report_from_row(row) if row is not None else None

    def list_reports(self, *, issue_identifier: str | None = None, cursor: str | None = None, limit: int = 50) -> dict:
        conditions = ["issue_identifier = ?"] if issue_identifier else []
        parameters = [issue_identifier] if issue_identifier else []
        return self._list_keyset_rows(
            "reports",
            conditions,
            parameters,
            cursor,
            limit,
            self._report_from_row,
            order_by="updated_at DESC, issue_identifier ASC, report_version DESC",
            cursor_fields=("updated_at", "issue_identifier", "report_version"),
            after_clause=(
                "updated_at < ? OR (updated_at = ? AND "
                "(issue_identifier > ? OR (issue_identifier = ? AND report_version < ?)))"
            ),
            after_parameters=lambda values: (
                values["updated_at"],
                values["updated_at"],
                values["issue_identifier"],
                values["issue_identifier"],
                values["report_version"],
            ),
        )

    def save_session(
        self, token_hash: str, *, expires_at: float, metadata: dict | None = None, created_at: float | None = None
    ) -> None:
        values = {
            "token_hash": token_hash,
            "expires_at": expires_at,
            "created_at": time.time() if created_at is None else created_at,
            "metadata_json": self._dump(bounded_redacted_data(metadata or {})),
        }
        self._write(
            lambda connection: connection.execute(
                """
                INSERT INTO dashboard_sessions (token_hash, expires_at, created_at, metadata_json)
                VALUES (:token_hash, :expires_at, :created_at, :metadata_json)
                ON CONFLICT(token_hash) DO UPDATE SET
                    expires_at = excluded.expires_at,
                    metadata_json = excluded.metadata_json
                """,
                values,
            )
        )

    def get_session(self, token_hash: str, *, now: float | None = None) -> dict | None:
        current_time = time.time() if now is None else now
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM dashboard_sessions WHERE token_hash = ?", (token_hash,)
            ).fetchone()
        if row is None or float(row["expires_at"]) <= current_time:
            if row is not None:
                self.delete_session(token_hash)
            return None
        return {
            "token_hash": row["token_hash"],
            "expires_at": row["expires_at"],
            "created_at": row["created_at"],
            "metadata": self._load(row["metadata_json"]),
        }

    def delete_session(self, token_hash: str) -> None:
        self._write(lambda connection: connection.execute("DELETE FROM dashboard_sessions WHERE token_hash = ?", (token_hash,)))

    def purge_expired_sessions(self, *, now: float | None = None) -> int:
        current_time = time.time() if now is None else now
        return self._write(
            lambda connection: connection.execute(
                "DELETE FROM dashboard_sessions WHERE expires_at <= ?", (current_time,)
            ).rowcount
        )

    def record_login_attempt(
        self,
        rate_limit_key: str,
        *,
        failures: int,
        window_started_at: float,
        locked_until: float | None = None,
    ) -> None:
        values = {
            "rate_limit_key": rate_limit_key,
            "failures": failures,
            "window_started_at": window_started_at,
            "locked_until": locked_until,
            "updated_at": time.time(),
        }
        self._write(
            lambda connection: connection.execute(
                """
                INSERT INTO login_attempts (rate_limit_key, failures, window_started_at, locked_until, updated_at)
                VALUES (:rate_limit_key, :failures, :window_started_at, :locked_until, :updated_at)
                ON CONFLICT(rate_limit_key) DO UPDATE SET
                    failures = excluded.failures,
                    window_started_at = excluded.window_started_at,
                    locked_until = excluded.locked_until,
                    updated_at = excluded.updated_at
                """,
                values,
            )
        )

    def record_failed_login_attempt(
        self,
        rate_limit_key: str,
        *,
        now: float | None = None,
        max_failures: int = 5,
        window_seconds: float = 300,
        lock_seconds: float = 900,
    ) -> dict:
        reservation = self.reserve_login_attempt(
            rate_limit_key,
            now=now,
            max_attempts=max_failures,
            window_seconds=window_seconds,
            lock_seconds=lock_seconds,
        )
        if reservation["reserved"]:
            self.complete_login_attempt(reservation["reservation_id"], succeeded=False, now=now)
        return {
            "failures": reservation["failures"],
            "locked_until": reservation["locked_until"],
            "locked": not reservation["reserved"] or reservation["locked_until"] is not None,
        }

    def reserve_login_attempt(
        self,
        rate_limit_key: str,
        *,
        now: float | None = None,
        max_attempts: int = 5,
        window_seconds: float = 300,
        lock_seconds: float = 900,
    ) -> dict:
        if max_attempts < 1 or window_seconds <= 0 or lock_seconds <= 0:
            raise ValueError("Login attempt limits must be positive")
        current_time = time.time() if now is None else float(now)

        def write(connection: sqlite3.Connection) -> dict:
            self._cleanup_expired_login_attempt_state(
                connection,
                now=current_time,
                retention_seconds=max(window_seconds, lock_seconds),
            )
            row = connection.execute(
                "SELECT * FROM login_attempts WHERE rate_limit_key = ?", (rate_limit_key,)
            ).fetchone()
            if row is not None and row["locked_until"] is not None and float(row["locked_until"]) > current_time:
                return {
                    "reserved": False,
                    "reservation_id": None,
                    "failures": int(row["failures"]),
                    "locked_until": row["locked_until"],
                }
            if row is None or current_time - float(row["window_started_at"]) >= window_seconds:
                failures = 0
                window_started_at = current_time
            else:
                failures = int(row["failures"])
                window_started_at = float(row["window_started_at"])
            if failures >= max_attempts:
                locked_until = current_time + lock_seconds
                connection.execute(
                    "UPDATE login_attempts SET locked_until = ?, updated_at = ? WHERE rate_limit_key = ?",
                    (locked_until, current_time, rate_limit_key),
                )
                return {
                    "reserved": False,
                    "reservation_id": None,
                    "failures": failures,
                    "locked_until": locked_until,
                }
            active_global_reservations = int(
                connection.execute(
                    "SELECT COUNT(*) FROM login_attempt_reservations WHERE expires_at > ?",
                    (current_time,),
                ).fetchone()[0]
            )
            if active_global_reservations >= self.LOGIN_GLOBAL_KDF_LIMIT:
                return {
                    "reserved": False,
                    "reservation_id": None,
                    "failures": failures,
                    "locked_until": row["locked_until"] if row is not None else None,
                }
            failures += 1
            locked_until = current_time + lock_seconds if failures >= max_attempts else None
            connection.execute(
                """
                INSERT INTO login_attempts (rate_limit_key, failures, window_started_at, locked_until, updated_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(rate_limit_key) DO UPDATE SET
                    failures = excluded.failures,
                    window_started_at = excluded.window_started_at,
                    locked_until = excluded.locked_until,
                    updated_at = excluded.updated_at
                """,
                (rate_limit_key, failures, window_started_at, locked_until, current_time),
            )
            reservation_id = secrets.token_urlsafe(24)
            connection.execute(
                """
                INSERT INTO login_attempt_reservations (
                    reservation_id, rate_limit_key, reserved_at, expires_at
                ) VALUES (?, ?, ?, ?)
                """,
                (
                    reservation_id,
                    rate_limit_key,
                    current_time,
                    current_time + self.LOGIN_RESERVATION_TTL_SECONDS,
                ),
            )
            return {
                "reserved": True,
                "reservation_id": reservation_id,
                "failures": failures,
                "locked_until": locked_until,
            }

        return self._write(write)

    def complete_login_attempt(
        self, reservation_id: str, *, succeeded: bool, now: float | None = None
    ) -> bool:
        current_time = time.time() if now is None else float(now)

        def write(connection: sqlite3.Connection) -> bool:
            self._cleanup_expired_login_attempt_state(connection, now=current_time)
            reservation = connection.execute(
                "SELECT rate_limit_key FROM login_attempt_reservations WHERE reservation_id = ?",
                (reservation_id,),
            ).fetchone()
            if reservation is None:
                return False
            deleted = connection.execute(
                "DELETE FROM login_attempt_reservations WHERE reservation_id = ?",
                (reservation_id,),
            ).rowcount
            if deleted != 1:
                return False
            rate_limit_key = reservation["rate_limit_key"]
            if succeeded:
                active_reservations = int(
                    connection.execute(
                        "SELECT COUNT(*) FROM login_attempt_reservations "
                        "WHERE rate_limit_key = ? AND expires_at > ?",
                        (rate_limit_key, current_time),
                    ).fetchone()[0]
                )
                if active_reservations:
                    connection.execute(
                        """
                        UPDATE login_attempts
                        SET failures = ?, window_started_at = ?, locked_until = NULL, updated_at = ?
                        WHERE rate_limit_key = ?
                        """,
                        (active_reservations, current_time, current_time, rate_limit_key),
                    )
                else:
                    connection.execute(
                        "DELETE FROM login_attempts WHERE rate_limit_key = ?", (rate_limit_key,)
                    )
            else:
                connection.execute(
                    "UPDATE login_attempts SET updated_at = ? WHERE rate_limit_key = ?",
                    (current_time, rate_limit_key),
                )
            return True

        return self._write(write)

    def get_login_attempt(self, rate_limit_key: str) -> dict | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM login_attempts WHERE rate_limit_key = ?", (rate_limit_key,)
            ).fetchone()
        return dict(row) if row is not None else None

    def clear_login_attempt(self, rate_limit_key: str) -> None:
        self._write(
            lambda connection: connection.execute("DELETE FROM login_attempts WHERE rate_limit_key = ?", (rate_limit_key,))
        )

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS issue_runs (
                    issue_identifier TEXT PRIMARY KEY,
                    issue_id TEXT,
                    title TEXT,
                    linear_state TEXT,
                    status TEXT,
                    attempt INTEGER,
                    workspace TEXT,
                    started_at REAL,
                    updated_at REAL NOT NULL,
                    completed_at REAL,
                    cancelled_at REAL,
                    codex_process_id TEXT,
                    codex_thread_id TEXT,
                    codex_turn_id TEXT,
                    codex_session_id TEXT,
                    branch TEXT,
                    commit_hash TEXT,
                    review_url TEXT,
                    report_url TEXT,
                    report_published_at REAL,
                    latest_error_summary TEXT,
                    error_count INTEGER NOT NULL DEFAULT 0,
                    details_json TEXT NOT NULL DEFAULT '{}'
                );
                CREATE TABLE IF NOT EXISTS runtime_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp REAL NOT NULL,
                    severity TEXT NOT NULL,
                    category TEXT NOT NULL,
                    type TEXT NOT NULL,
                    issue_identifier TEXT,
                    message TEXT NOT NULL,
                    data_json TEXT NOT NULL DEFAULT '{}'
                );
                CREATE INDEX IF NOT EXISTS runtime_events_issue_timestamp ON runtime_events(issue_identifier, timestamp DESC);
                CREATE INDEX IF NOT EXISTS runtime_events_severity ON runtime_events(severity);
                CREATE INDEX IF NOT EXISTS runtime_events_type ON runtime_events(type);
                CREATE TABLE IF NOT EXISTS runtime_errors (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    issue_identifier TEXT,
                    session_id TEXT,
                    stage TEXT NOT NULL,
                    error_type TEXT NOT NULL,
                    message TEXT NOT NULL,
                    retryable INTEGER NOT NULL DEFAULT 0,
                    attempt INTEGER,
                    timestamp REAL NOT NULL,
                    context_json TEXT NOT NULL DEFAULT '{}',
                    resolved_at REAL,
                    resolving_event TEXT
                );
                CREATE INDEX IF NOT EXISTS runtime_errors_issue_timestamp ON runtime_errors(issue_identifier, timestamp DESC);
                CREATE INDEX IF NOT EXISTS runtime_errors_unresolved ON runtime_errors(resolved_at, timestamp DESC);
                CREATE TABLE IF NOT EXISTS reports (
                    issue_identifier TEXT NOT NULL,
                    report_version INTEGER NOT NULL,
                    json_path TEXT,
                    html_path TEXT,
                    url TEXT,
                    review_metadata_json TEXT NOT NULL DEFAULT '{}',
                    linear_comment_id TEXT,
                    linear_sync_status TEXT NOT NULL DEFAULT 'pending',
                    retry_count INTEGER NOT NULL DEFAULT 0,
                    next_retry_at REAL,
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL,
                    details_json TEXT NOT NULL DEFAULT '{}',
                    PRIMARY KEY (issue_identifier, report_version)
                );
                CREATE TABLE IF NOT EXISTS dashboard_sessions (
                    token_hash TEXT PRIMARY KEY,
                    expires_at REAL NOT NULL,
                    created_at REAL NOT NULL,
                    metadata_json TEXT NOT NULL DEFAULT '{}'
                );
                CREATE TABLE IF NOT EXISTS login_attempts (
                    rate_limit_key TEXT PRIMARY KEY,
                    failures INTEGER NOT NULL CHECK (failures >= 0),
                    window_started_at REAL NOT NULL,
                    locked_until REAL,
                    updated_at REAL NOT NULL
                );
                CREATE TABLE IF NOT EXISTS login_attempt_reservations (
                    reservation_id TEXT PRIMARY KEY,
                    rate_limit_key TEXT NOT NULL,
                    reserved_at REAL NOT NULL,
                    expires_at REAL NOT NULL,
                    FOREIGN KEY (rate_limit_key) REFERENCES login_attempts(rate_limit_key) ON DELETE CASCADE
                );
                CREATE INDEX IF NOT EXISTS login_attempt_reservations_expiry
                    ON login_attempt_reservations(expires_at);
                CREATE INDEX IF NOT EXISTS login_attempt_reservations_key
                    ON login_attempt_reservations(rate_limit_key);
                CREATE TABLE IF NOT EXISTS task_page_snapshots (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    created_at REAL NOT NULL
                );
                CREATE INDEX IF NOT EXISTS task_page_snapshots_created_at
                    ON task_page_snapshots(created_at);
                CREATE TABLE IF NOT EXISTS task_page_snapshot_entries (
                    snapshot_id INTEGER NOT NULL,
                    position INTEGER NOT NULL,
                    issue_identifier TEXT NOT NULL,
                    PRIMARY KEY (snapshot_id, position),
                    FOREIGN KEY (snapshot_id) REFERENCES task_page_snapshots(id) ON DELETE CASCADE
                );
                """
            )
            columns = {row["name"] for row in connection.execute("PRAGMA table_info(reports)")}
            if "linear_sync_status" not in columns:
                connection.execute("ALTER TABLE reports ADD COLUMN linear_sync_status TEXT NOT NULL DEFAULT 'pending'")
                if "sync_status" in columns:
                    connection.execute(
                        "UPDATE reports SET linear_sync_status = sync_status WHERE sync_status IS NOT NULL"
                    )
            self._legacy_report_sync_status = "sync_status" in columns

    def _cleanup_expired_login_attempt_state(
        self,
        connection: sqlite3.Connection,
        *,
        now: float,
        retention_seconds: float = 900.0,
    ) -> None:
        connection.execute(
            """
            DELETE FROM login_attempt_reservations
            WHERE reservation_id IN (
                SELECT reservation_id
                FROM login_attempt_reservations
                WHERE expires_at <= ?
                ORDER BY expires_at, reservation_id
                LIMIT ?
            )
            """,
            (now, self.LOGIN_ATTEMPT_CLEANUP_BATCH_SIZE),
        )
        connection.execute(
            """
            DELETE FROM login_attempts
            WHERE rate_limit_key IN (
                SELECT attempts.rate_limit_key
                FROM login_attempts AS attempts
                WHERE attempts.updated_at <= ?
                  AND (attempts.locked_until IS NULL OR attempts.locked_until <= ?)
                  AND NOT EXISTS (
                      SELECT 1
                      FROM login_attempt_reservations AS reservations
                      WHERE reservations.rate_limit_key = attempts.rate_limit_key
                  )
                ORDER BY attempts.updated_at, attempts.rate_limit_key
                LIMIT ?
            )
            """,
            (now - retention_seconds, now, self.LOGIN_ATTEMPT_CLEANUP_BATCH_SIZE),
        )

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=5, isolation_level=None)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA busy_timeout = 5000")
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA journal_mode = WAL")
        return connection

    def _write(self, operation):
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            result = operation(connection)
            connection.commit()
            return result
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def _issue_values(self, entry: dict, existing: sqlite3.Row | None, now: float) -> dict:
        existing_details = self._load(existing["details_json"]) if existing is not None else {}
        safe_entry = bounded_redacted_data(entry)
        details = {**existing_details, **safe_entry}
        aliases = {"identifier": "issue_identifier", "state": "linear_state", "branch_name": "branch", "commit": "commit_hash"}
        fields = (
            "issue_id",
            "title",
            "linear_state",
            "status",
            "attempt",
            "workspace",
            "started_at",
            "updated_at",
            "completed_at",
            "cancelled_at",
            "codex_process_id",
            "codex_thread_id",
            "codex_turn_id",
            "codex_session_id",
            "branch",
            "commit_hash",
            "review_url",
            "report_url",
            "report_published_at",
            "latest_error_summary",
            "error_count",
        )
        values = {"issue_identifier": str(entry.get("issue_identifier") or entry.get("identifier"))}
        for field in fields:
            source = field
            if field not in entry:
                source = next((alias for alias, target in aliases.items() if target == field and alias in entry), field)
            if source in entry:
                values[field] = entry[source]
            elif field == "updated_at":
                values[field] = now
            elif existing is not None:
                values[field] = existing[field]
            elif field == "error_count":
                values[field] = 0
            else:
                values[field] = None
        values["details_json"] = self._dump(details)
        return values

    def _record_error(self, connection: sqlite3.Connection, error: RuntimeErrorRecord) -> int:
        context = bounded_redacted_data(error.context)
        cursor = connection.execute(
            """
            INSERT INTO runtime_errors (
                issue_identifier, session_id, stage, error_type, message, retryable, attempt, timestamp, context_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                error.issue_identifier,
                error.session_id,
                error.stage,
                error.error_type,
                error.message,
                int(error.retryable),
                error.attempt,
                error.timestamp,
                self._dump(context),
            ),
        )
        if error.issue_identifier:
            connection.execute(
                """
                UPDATE issue_runs
                SET latest_error_summary = ?, error_count = error_count + 1, updated_at = ?
                WHERE issue_identifier = ?
                """,
                (error.message, time.time(), error.issue_identifier),
            )
        return int(cursor.lastrowid)

    def _list_keyset_rows(
        self,
        table: str,
        conditions: list[str],
        parameters: list[object],
        cursor: str | None,
        limit: int,
        converter,
        *,
        order_by: str,
        cursor_fields: tuple[str, ...],
        after_clause: str,
        after_parameters,
        select_columns: str = "*",
    ) -> dict:
        page_limit = self._limit(limit)
        cursor_values = self._decode_cursor(cursor, cursor_fields)
        page_conditions = list(conditions)
        page_parameters = list(parameters)
        if cursor_values is not None:
            page_conditions.append(f"({after_clause})")
            page_parameters.extend(after_parameters(cursor_values))
        where = f"WHERE {' AND '.join(page_conditions)}" if page_conditions else ""
        with self._connect() as connection:
            rows = connection.execute(
                f"SELECT {select_columns} FROM {table} {where} ORDER BY {order_by} LIMIT ?",
                (*page_parameters, page_limit + 1),
            ).fetchall()
        page_rows = rows[:page_limit]
        next_cursor = None
        if len(rows) > page_limit and page_rows:
            next_cursor = self._encode_cursor({field: page_rows[-1][field] for field in cursor_fields})
        return {"items": [converter(row) for row in page_rows], "next_cursor": next_cursor}

    def _list_task_snapshot(
        self,
        conditions: list[str],
        parameters: list[object],
        cursor: str | None,
        limit: int,
    ) -> dict:
        page_limit = self._limit(limit)
        if cursor is None:
            where = f"WHERE {' AND '.join(conditions)}" if conditions else ""

            def write(connection: sqlite3.Connection) -> dict:
                self._cleanup_expired_task_page_snapshots(connection)
                rows = connection.execute(
                    f"SELECT * FROM issue_runs {where} ORDER BY updated_at DESC, issue_identifier ASC",
                    parameters,
                ).fetchall()
                page_rows = rows[:page_limit]
                if len(rows) <= page_limit:
                    return {"items": [self._task_from_row(row) for row in page_rows], "next_cursor": None}
                snapshot_id = connection.execute(
                    "INSERT INTO task_page_snapshots (created_at) VALUES (?)", (time.time(),)
                ).lastrowid
                connection.executemany(
                    """
                    INSERT INTO task_page_snapshot_entries (snapshot_id, position, issue_identifier)
                    VALUES (?, ?, ?)
                    """,
                    ((snapshot_id, position, row["issue_identifier"]) for position, row in enumerate(rows)),
                )
                return {
                    "items": [self._task_from_row(row) for row in page_rows],
                    "next_cursor": self._encode_cursor({"snapshot_id": snapshot_id, "position": page_limit - 1}),
                }

            return self._write(write)

        values = self._decode_cursor(cursor, ("snapshot_id", "position"))
        def read(connection: sqlite3.Connection) -> dict | None:
            self._cleanup_expired_task_page_snapshots(connection)
            snapshot = connection.execute(
                "SELECT id FROM task_page_snapshots WHERE id = ?", (values["snapshot_id"],)
            ).fetchone()
            if snapshot is None:
                return None
            rows = connection.execute(
                """
                SELECT task_page_snapshot_entries.position AS snapshot_position, issue_runs.*
                FROM task_page_snapshot_entries
                JOIN issue_runs ON issue_runs.issue_identifier = task_page_snapshot_entries.issue_identifier
                WHERE task_page_snapshot_entries.snapshot_id = ? AND task_page_snapshot_entries.position > ?
                ORDER BY task_page_snapshot_entries.position ASC
                LIMIT ?
                """,
                (values["snapshot_id"], values["position"], page_limit + 1),
            ).fetchall()
            page_rows = rows[:page_limit]
            next_cursor = None
            if len(rows) > page_limit and page_rows:
                next_cursor = self._encode_cursor(
                    {"snapshot_id": values["snapshot_id"], "position": page_rows[-1]["snapshot_position"]}
                )
            return {"items": [self._task_from_row(row) for row in page_rows], "next_cursor": next_cursor}

        result = self._write(read)
        if result is None:
            raise RuntimeStoreInputError("Expired pagination cursor")
        return result

    def _cleanup_expired_task_page_snapshots(self, connection: sqlite3.Connection) -> None:
        cutoff = time.time() - self.TASK_PAGE_SNAPSHOT_TTL_SECONDS
        connection.execute(
            """
            DELETE FROM task_page_snapshots
            WHERE id IN (
                SELECT id
                FROM task_page_snapshots
                WHERE created_at < ?
                ORDER BY created_at ASC, id ASC
                LIMIT ?
            )
            """,
            (cutoff, self.TASK_PAGE_SNAPSHOT_CLEANUP_BATCH_SIZE),
        )

    @staticmethod
    def _decode_cursor(cursor: str | None, fields: tuple[str, ...]) -> dict | None:
        if cursor is None:
            return None
        if not isinstance(cursor, str) or not cursor:
            raise RuntimeStoreInputError("Invalid pagination cursor")
        try:
            padding = "=" * (-len(cursor) % 4)
            payload = json.loads(base64.urlsafe_b64decode(cursor + padding).decode("utf-8"))
            if not isinstance(payload, dict) or set(payload) != set(fields):
                raise ValueError("unexpected cursor fields")
            for field, value in payload.items():
                if field == "issue_identifier":
                    if not isinstance(value, str):
                        raise ValueError("invalid issue identifier")
                elif isinstance(value, bool) or not isinstance(value, (int, float)):
                    raise ValueError("invalid ordering value")
            return payload
        except (binascii.Error, UnicodeDecodeError, ValueError, TypeError) as error:
            raise RuntimeStoreInputError("Invalid pagination cursor") from error

    @staticmethod
    def _encode_cursor(values: dict) -> str:
        payload = json.dumps(values, separators=(",", ":"), sort_keys=True).encode("utf-8")
        return base64.urlsafe_b64encode(payload).decode("ascii").rstrip("=")

    @staticmethod
    def _limit(limit: int) -> int:
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 200:
            raise RuntimeStoreInputError("Invalid pagination limit: expected an integer from 1 to 200")
        return limit

    def _task_from_row(self, row: sqlite3.Row) -> dict:
        result = self._load(row["details_json"])
        result.update({key: row[key] for key in row.keys() if key not in {"details_json", "snapshot_position"}})
        result["commit"] = result.get("commit_hash")
        return result

    def _event_from_row(self, row: sqlite3.Row) -> dict:
        return {
            "id": row["id"],
            "timestamp": row["timestamp"],
            "severity": row["severity"],
            "category": row["category"],
            "type": row["type"],
            "issue_identifier": row["issue_identifier"],
            "message": row["message"],
            "data": self._load(row["data_json"]),
        }

    def _error_from_row(self, row: sqlite3.Row) -> dict:
        return {
            "id": row["id"],
            "issue_identifier": row["issue_identifier"],
            "session_id": row["session_id"],
            "stage": row["stage"],
            "error_type": row["error_type"],
            "message": row["message"],
            "retryable": bool(row["retryable"]),
            "attempt": row["attempt"],
            "timestamp": row["timestamp"],
            "context": self._load(row["context_json"]),
            "resolved_at": row["resolved_at"],
            "resolving_event": row["resolving_event"],
        }

    def _report_from_row(self, row: sqlite3.Row) -> dict:
        result = self._load(row["details_json"])
        result.pop("sync_status", None)
        result.pop("linear_sync_status", None)
        result.update(
            {
                key: row[key]
                for key in row.keys()
                if key not in {"details_json", "review_metadata_json", "sync_status"}
            }
        )
        result["review_metadata"] = self._load(row["review_metadata_json"])
        return result

    @staticmethod
    def _dump(value: object) -> str:
        return json.dumps(value, sort_keys=True, default=str)

    @staticmethod
    def _load(value: str) -> dict:
        parsed = json.loads(value)
        return parsed if isinstance(parsed, dict) else {}
