from __future__ import annotations

import json
import queue
import sqlite3
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, cast

DB_SCHEMA_VERSION = 5
DEFAULT_LOW_PRIORITY_LOG_QUEUE_SIZE = 5000
HIGH_PRIORITY_QUEUE_RESERVE = 256


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


CREATE_SCHEMA_MIGRATIONS_SQL = """
CREATE TABLE IF NOT EXISTS schema_migrations (
    version INTEGER PRIMARY KEY,
    applied_at TEXT NOT NULL
);
"""


def _create_base_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS models (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            provider_id TEXT NOT NULL,
            endpoint_id TEXT DEFAULT NULL,
            provider_model_id TEXT NOT NULL,
            provider_base_url TEXT NOT NULL,
            provider_api_key_env TEXT NOT NULL,
            provider_options_json TEXT DEFAULT NULL,
            context_window INTEGER DEFAULT 4096,
            max_output_tokens INTEGER DEFAULT NULL,
            tokenizer_family TEXT DEFAULT NULL,
            supports_tools INTEGER DEFAULT 0,
            supports_streaming INTEGER DEFAULT 1,
            supports_vision INTEGER DEFAULT 0,
            supports_structured_output INTEGER DEFAULT 0,
            supports_system_messages INTEGER DEFAULT 1,
            chatbot_arena_elo REAL DEFAULT NULL,
            open_llm_score REAL DEFAULT NULL,
            openrouter_rank INTEGER DEFAULT NULL,
            is_healthy INTEGER DEFAULT 1,
            last_health_check TEXT DEFAULT NULL,
            avg_latency_ms REAL DEFAULT NULL,
            avg_ttfb_ms REAL DEFAULT NULL,
            consecutive_failures INTEGER DEFAULT 0,
            backoff_level INTEGER DEFAULT 0,
            cooldown_until TEXT DEFAULT NULL,
            last_error TEXT DEFAULT NULL,
            last_probe_at TEXT DEFAULT NULL,
            last_success_at TEXT DEFAULT NULL,
            last_failure_at TEXT DEFAULT NULL,
            last_routed_at TEXT DEFAULT NULL,
            composite_score REAL DEFAULT 0.0,
            score_updated_at TEXT DEFAULT NULL,
            discovered_at TEXT NOT NULL,
            last_seen_at TEXT NOT NULL,
            is_active INTEGER DEFAULT 1
        );

        CREATE TABLE IF NOT EXISTS request_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            request_id TEXT DEFAULT NULL,
            timestamp TEXT NOT NULL,
            request_source TEXT NOT NULL DEFAULT 'client',
            selected_model_id TEXT NOT NULL,
            provider_id TEXT NOT NULL,
            selected_provider_model_id TEXT DEFAULT NULL,
            selected_tokenizer_family TEXT DEFAULT NULL,
            client_requested_model TEXT DEFAULT NULL,
            attempt_index INTEGER DEFAULT 0,
            was_fallback INTEGER DEFAULT 0,
            estimated_prompt_tokens INTEGER DEFAULT NULL,
            selected_context_window INTEGER DEFAULT NULL,
            prompt_tokens INTEGER DEFAULT NULL,
            completion_tokens INTEGER DEFAULT NULL,
            total_tokens INTEGER DEFAULT NULL,
            latency_ms REAL DEFAULT NULL,
            ttfb_ms REAL DEFAULT NULL,
            success INTEGER DEFAULT 1,
            gateway_error_category TEXT DEFAULT NULL,
            error_code TEXT DEFAULT NULL,
            error_message TEXT DEFAULT NULL,
            was_streaming INTEGER DEFAULT 0,
            had_tools INTEGER DEFAULT 0,
            had_vision INTEGER DEFAULT 0
        );

        CREATE TABLE IF NOT EXISTS leaderboard_cache (
            model_name_normalized TEXT PRIMARY KEY,
            chatbot_arena_elo REAL DEFAULT NULL,
            open_llm_avg_score REAL DEFAULT NULL,
            fetched_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS config_overrides (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        """
    )


def migrate_to_v1(conn: sqlite3.Connection) -> None:
    _create_base_schema(conn)


def migrate_to_v2(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        DROP INDEX IF EXISTS idx_models_routing;
        DROP INDEX IF EXISTS idx_models_provider_active;
        DROP INDEX IF EXISTS idx_request_log_timestamp;
        DROP INDEX IF EXISTS idx_request_log_model;
        DROP INDEX IF EXISTS idx_request_log_provider_day;

        CREATE INDEX IF NOT EXISTS idx_models_routing
            ON models (is_active, is_healthy, cooldown_until, composite_score DESC);
        CREATE INDEX IF NOT EXISTS idx_models_provider_active
            ON models (provider_id, is_active, last_seen_at DESC);

        CREATE INDEX IF NOT EXISTS idx_request_log_timestamp
            ON request_log (timestamp DESC);
        CREATE INDEX IF NOT EXISTS idx_request_log_model
            ON request_log (selected_model_id);
        CREATE INDEX IF NOT EXISTS idx_request_log_provider_day
            ON request_log (provider_id, request_source, timestamp DESC);
        """
    )


def migrate_to_v3(conn: sqlite3.Connection) -> None:
    # Ensure override/cache timestamps are canonical UTC Z timestamps rather than SQLite CURRENT_TIMESTAMP format.
    conn.execute(
        """
        UPDATE config_overrides
        SET updated_at = REPLACE(updated_at, ' ', 'T') || 'Z'
        WHERE updated_at GLOB '????-??-?? ??:??:??'
        """
    )
    conn.execute(
        """
        UPDATE leaderboard_cache
        SET fetched_at = REPLACE(fetched_at, ' ', 'T') || 'Z'
        WHERE fetched_at GLOB '????-??-?? ??:??:??'
        """
    )


def _add_column_if_missing(
    conn: sqlite3.Connection,
    table_name: str,
    column_name: str,
    column_sql: str,
) -> None:
    columns = {str(row[1]) for row in conn.execute(f"PRAGMA table_info({table_name})").fetchall()}
    if column_name in columns:
        return
    conn.execute(f"ALTER TABLE {table_name} ADD COLUMN {column_sql}")


def migrate_to_v4(conn: sqlite3.Connection) -> None:
    _add_column_if_missing(
        conn,
        "request_log",
        "selected_provider_model_id",
        "selected_provider_model_id TEXT DEFAULT NULL",
    )
    _add_column_if_missing(
        conn,
        "request_log",
        "selected_tokenizer_family",
        "selected_tokenizer_family TEXT DEFAULT NULL",
    )
    _add_column_if_missing(
        conn,
        "request_log",
        "estimated_prompt_tokens",
        "estimated_prompt_tokens INTEGER DEFAULT NULL",
    )


def migrate_to_v5(conn: sqlite3.Connection) -> None:
    _add_column_if_missing(
        conn,
        "request_log",
        "selected_context_window",
        "selected_context_window INTEGER DEFAULT NULL",
    )


MIGRATIONS: list[tuple[int, Any]] = [
    (1, migrate_to_v1),
    (2, migrate_to_v2),
    (3, migrate_to_v3),
    (4, migrate_to_v4),
    (5, migrate_to_v5),
]


@dataclass(slots=True)
class WriteTask:
    sql: str
    params: tuple[Any, ...]
    is_low_priority_log: bool = False


class DBWriter:
    def __init__(
        self,
        db_path: str,
        *,
        busy_timeout_ms: int = 5000,
        low_priority_log_enabled: bool = True,
        low_priority_log_queue_size: int = DEFAULT_LOW_PRIORITY_LOG_QUEUE_SIZE,
    ) -> None:
        self.db_path = db_path
        self.busy_timeout_ms = busy_timeout_ms
        self._low_priority_log_enabled = low_priority_log_enabled
        self._low_priority_log_queue_size = max(int(low_priority_log_queue_size), 1)
        self._pending_low_priority_logs = 0
        self._dropped_low_priority_logs = 0
        self._lock = threading.Lock()
        self._queue: queue.Queue[WriteTask | None] = queue.Queue(
            maxsize=self._low_priority_log_queue_size + HIGH_PRIORITY_QUEUE_RESERVE
        )
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._started = False

    def start(self) -> None:
        if self._started:
            return
        self._started = True
        self._thread.start()

    def stop(self) -> None:
        if not self._started:
            return
        self._queue.put(None)
        self._thread.join(timeout=2)

    def set_low_priority_log_policy(self, *, enabled: bool, queue_size: int) -> None:
        with self._lock:
            self._low_priority_log_enabled = enabled
            self._low_priority_log_queue_size = max(int(queue_size), 1)

    def enqueue(
        self,
        sql: str,
        params: tuple[Any, ...] = (),
        *,
        is_low_priority_log: bool = False,
    ) -> bool:
        task = WriteTask(
            sql=sql,
            params=params,
            is_low_priority_log=is_low_priority_log,
        )
        if is_low_priority_log:
            with self._lock:
                if not self._low_priority_log_enabled:
                    self._dropped_low_priority_logs += 1
                    return False
                if self._pending_low_priority_logs >= self._low_priority_log_queue_size:
                    self._dropped_low_priority_logs += 1
                    return False
                self._pending_low_priority_logs += 1
            try:
                self._queue.put_nowait(task)
                return True
            except queue.Full:
                with self._lock:
                    self._pending_low_priority_logs = max(self._pending_low_priority_logs - 1, 0)
                    self._dropped_low_priority_logs += 1
                return False

        self._queue.put(task)
        return True

    def _run(self) -> None:
        conn = sqlite3.connect(self.db_path, check_same_thread=False)
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute(f"PRAGMA busy_timeout={self.busy_timeout_ms};")
        conn.commit()
        while True:
            task = self._queue.get()
            if task is None:
                self._queue.task_done()
                break
            try:
                conn.execute(task.sql, task.params)
                conn.commit()
            finally:
                if task.is_low_priority_log:
                    with self._lock:
                        self._pending_low_priority_logs = max(
                            self._pending_low_priority_logs - 1,
                            0,
                        )
                self._queue.task_done()
        conn.close()

    def flush(self) -> None:
        self._queue.join()

    def queue_depth(self) -> int:
        return self._queue.qsize()

    def queue_capacity(self) -> int:
        return self._queue.maxsize

    def dropped_low_priority_logs(self) -> int:
        with self._lock:
            return self._dropped_low_priority_logs


class Database:
    def __init__(
        self,
        db_path: str,
        *,
        busy_timeout_ms: int = 5000,
        request_log_enabled: bool = True,
        request_log_queue_size: int = 5000,
    ) -> None:
        self.db_path = db_path
        self.busy_timeout_ms = busy_timeout_ms
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self.writer = DBWriter(
            db_path,
            busy_timeout_ms=busy_timeout_ms,
            low_priority_log_enabled=request_log_enabled,
            low_priority_log_queue_size=request_log_queue_size,
        )

    def _connect(self, *, check_same_thread: bool = True) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, check_same_thread=check_same_thread)
        conn.execute(f"PRAGMA busy_timeout={self.busy_timeout_ms};")
        return conn

    def init(self) -> None:
        conn = self._connect()
        try:
            conn.execute("PRAGMA journal_mode=WAL;")
            conn.execute("PRAGMA foreign_keys=ON;")
            conn.execute("BEGIN")
            conn.execute(CREATE_SCHEMA_MIGRATIONS_SQL)
            applied_versions = {
                int(row[0])
                for row in conn.execute("SELECT version FROM schema_migrations").fetchall()
            }
            for version, migrate in MIGRATIONS:
                if version in applied_versions:
                    continue
                migrate(conn)
                conn.execute(
                    "INSERT INTO schema_migrations(version, applied_at) VALUES (?, ?)",
                    (version, utc_now_iso()),
                )
            conn.commit()
        finally:
            conn.close()

    @contextmanager
    def read_conn(self) -> Iterator[sqlite3.Connection]:
        conn = self._connect()
        conn.row_factory = sqlite3.Row
        try:
            yield conn
        finally:
            conn.close()

    def get_overrides(self) -> dict[str, Any]:
        with self.read_conn() as conn:
            rows = conn.execute("SELECT key, value FROM config_overrides").fetchall()
        out: dict[str, Any] = {}
        for row in rows:
            try:
                out[str(row["key"])] = json.loads(row["value"])
            except json.JSONDecodeError:
                out[str(row["key"])] = row["value"]
        return out

    def list_overrides(self) -> list[sqlite3.Row]:
        with self.read_conn() as conn:
            return conn.execute(
                "SELECT key, value, updated_at FROM config_overrides ORDER BY key"
            ).fetchall()

    def set_override(self, key: str, value: Any) -> None:
        serialized = value if isinstance(value, str) else json.dumps(value)
        self.writer.enqueue(
            """
            INSERT INTO config_overrides(key, value, updated_at)
            VALUES (?, ?, ?)
            ON CONFLICT(key) DO UPDATE SET
                value=excluded.value,
                updated_at=excluded.updated_at
            """,
            (key, serialized, utc_now_iso()),
        )

    def delete_override(self, key: str) -> None:
        self.writer.enqueue("DELETE FROM config_overrides WHERE key=?", (key,))

    def configure_logging(self, *, request_log_enabled: bool, request_log_queue_size: int) -> None:
        self.writer.set_low_priority_log_policy(
            enabled=request_log_enabled,
            queue_size=request_log_queue_size,
        )

    def set_model_active(self, model_id: str, *, is_active: bool) -> None:
        self.writer.enqueue(
            "UPDATE models SET is_active=? WHERE id=?",
            (1 if is_active else 0, model_id),
        )

    def mark_models_not_seen(self, provider_id: str, seen_ids: list[str]) -> None:
        if seen_ids:
            placeholders = ",".join("?" for _ in seen_ids)
            sql = (
                "UPDATE models SET is_active=0 "
                f"WHERE provider_id=? AND id NOT IN ({placeholders})"
            )
            params = (provider_id, *seen_ids)
        else:
            sql = "UPDATE models SET is_active=0 WHERE provider_id=?"
            params = (provider_id,)
        self.writer.enqueue(sql, tuple(params))

    def log_request(self, entry: dict[str, Any]) -> bool:
        request_source = str(entry.get("request_source", "client"))
        return self.writer.enqueue(
            """
            INSERT INTO request_log(
                request_id, timestamp, request_source, selected_model_id, provider_id,
                selected_provider_model_id, selected_tokenizer_family, client_requested_model,
                attempt_index, was_fallback, estimated_prompt_tokens, selected_context_window,
                prompt_tokens,
                completion_tokens, total_tokens, latency_ms, ttfb_ms, success,
                gateway_error_category, error_code, error_message, was_streaming, had_tools, had_vision
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                entry.get("request_id"),
                entry.get("timestamp", utc_now_iso()),
                request_source,
                entry["selected_model_id"],
                entry["provider_id"],
                entry.get("selected_provider_model_id"),
                entry.get("selected_tokenizer_family"),
                entry.get("client_requested_model"),
                int(entry.get("attempt_index", 0) or 0),
                1 if entry.get("was_fallback") else 0,
                entry.get("estimated_prompt_tokens"),
                entry.get("selected_context_window"),
                entry.get("prompt_tokens"),
                entry.get("completion_tokens"),
                entry.get("total_tokens"),
                entry.get("latency_ms"),
                entry.get("ttfb_ms"),
                1 if entry.get("success", True) else 0,
                entry.get("gateway_error_category"),
                entry.get("error_code"),
                entry.get("error_message"),
                1 if entry.get("was_streaming") else 0,
                1 if entry.get("had_tools") else 0,
                1 if entry.get("had_vision") else 0,
            ),
            is_low_priority_log=request_source == "client",
        )

    def get_model_tokenization_metadata(self, model_id: str) -> sqlite3.Row | None:
        with self.read_conn() as conn:
            row = conn.execute(
                """
                SELECT provider_model_id, tokenizer_family, context_window
                FROM models
                WHERE id=?
                """,
                (model_id,),
            ).fetchone()
        return cast(sqlite3.Row | None, row)

    def upsert_leaderboard_cache(
        self,
        model_name_normalized: str,
        *,
        chatbot_arena_elo: float | None,
        open_llm_avg_score: float | None,
    ) -> None:
        self.writer.enqueue(
            """
            INSERT INTO leaderboard_cache(
                model_name_normalized, chatbot_arena_elo, open_llm_avg_score, fetched_at
            ) VALUES (?, ?, ?, ?)
            ON CONFLICT(model_name_normalized) DO UPDATE SET
                chatbot_arena_elo=COALESCE(excluded.chatbot_arena_elo, leaderboard_cache.chatbot_arena_elo),
                open_llm_avg_score=COALESCE(excluded.open_llm_avg_score, leaderboard_cache.open_llm_avg_score),
                fetched_at=excluded.fetched_at
            """,
            (model_name_normalized, chatbot_arena_elo, open_llm_avg_score, utc_now_iso()),
        )

    def get_leaderboard_cache(
        self, model_name_normalized: str, *, max_age_hours: int = 24
    ) -> sqlite3.Row | None:
        cutoff = (
            (datetime.now(timezone.utc) - timedelta(hours=max_age_hours))
            .replace(microsecond=0)
            .isoformat()
            .replace("+00:00", "Z")
        )
        with self.read_conn() as conn:
            row = conn.execute(
                """
                SELECT model_name_normalized, chatbot_arena_elo, open_llm_avg_score, fetched_at
                FROM leaderboard_cache
                WHERE model_name_normalized=? AND fetched_at >= ?
                """,
                (model_name_normalized, cutoff),
            ).fetchone()
        return cast(sqlite3.Row | None, row)

    def prune_old_logs(self, *, retention_days: int) -> int:
        cutoff = (
            (datetime.now(timezone.utc) - timedelta(days=retention_days))
            .replace(microsecond=0)
            .isoformat()
            .replace("+00:00", "Z")
        )
        self.writer.flush()
        conn = self._connect()
        try:
            result = conn.execute("DELETE FROM request_log WHERE timestamp < ?", (cutoff,))
            conn.commit()
            return max(int(result.rowcount or 0), 0)
        finally:
            conn.close()
