from __future__ import annotations

import json
import queue
import sqlite3
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS schema_migrations (
    version TEXT PRIMARY KEY,
    applied_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

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

CREATE INDEX IF NOT EXISTS idx_models_routing
    ON models (is_active, is_healthy, cooldown_until, composite_score DESC);
CREATE INDEX IF NOT EXISTS idx_models_provider_active
    ON models (provider_id, is_active, last_seen_at DESC);

CREATE TABLE IF NOT EXISTS request_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    request_id TEXT DEFAULT NULL,
    timestamp TEXT NOT NULL,
    request_source TEXT NOT NULL DEFAULT 'client',
    selected_model_id TEXT NOT NULL,
    provider_id TEXT NOT NULL,
    client_requested_model TEXT DEFAULT NULL,
    attempt_index INTEGER DEFAULT 0,
    was_fallback INTEGER DEFAULT 0,
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
CREATE INDEX IF NOT EXISTS idx_request_log_timestamp ON request_log (timestamp DESC);
CREATE INDEX IF NOT EXISTS idx_request_log_model ON request_log (selected_model_id);
CREATE INDEX IF NOT EXISTS idx_request_log_provider_day ON request_log (provider_id, request_source, timestamp DESC);

CREATE TABLE IF NOT EXISTS leaderboard_cache (
    model_name_normalized TEXT PRIMARY KEY,
    chatbot_arena_elo REAL DEFAULT NULL,
    open_llm_avg_score REAL DEFAULT NULL,
    fetched_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS config_overrides (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
"""


@dataclass(slots=True)
class WriteTask:
    sql: str
    params: tuple[Any, ...]


class DBWriter:
    def __init__(self, db_path: str) -> None:
        self.db_path = db_path
        self._queue: queue.Queue[WriteTask | None] = queue.Queue()
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

    def enqueue(self, sql: str, params: tuple[Any, ...] = ()) -> None:
        self._queue.put(WriteTask(sql=sql, params=params))

    def _run(self) -> None:
        conn = sqlite3.connect(self.db_path, check_same_thread=False)
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.commit()
        while True:
            task = self._queue.get()
            if task is None:
                self._queue.task_done()
                break
            conn.execute(task.sql, task.params)
            conn.commit()
            self._queue.task_done()
        conn.close()

    def flush(self) -> None:
        self._queue.join()


class Database:
    def __init__(self, db_path: str) -> None:
        self.db_path = db_path
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self.writer = DBWriter(db_path)

    def init(self) -> None:
        with sqlite3.connect(self.db_path) as conn:
            conn.executescript(SCHEMA_SQL)
            conn.execute("INSERT OR IGNORE INTO schema_migrations(version) VALUES (?)", ("v2_spec_schema",))
            conn.commit()

    def read_conn(self) -> sqlite3.Connection:
        return sqlite3.connect(self.db_path)

    def get_overrides(self) -> dict[str, Any]:
        with self.read_conn() as conn:
            rows = conn.execute("SELECT key, value FROM config_overrides").fetchall()
        out: dict[str, Any] = {}
        for key, value in rows:
            try:
                out[key] = json.loads(value)
            except json.JSONDecodeError:
                out[key] = value
        return out
