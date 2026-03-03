from __future__ import annotations

from dataclasses import dataclass
from queue import Empty, Queue
import sqlite3
import threading
from typing import Any


MIGRATIONS: list[tuple[str, str]] = [
    (
        "0001_init",
        """
        CREATE TABLE IF NOT EXISTS schema_migrations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            version TEXT NOT NULL UNIQUE,
            applied_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS models (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            provider TEXT NOT NULL,
            model_name TEXT NOT NULL,
            display_name TEXT,
            supports_tools INTEGER NOT NULL DEFAULT 0,
            supports_vision INTEGER NOT NULL DEFAULT 0,
            supports_streaming INTEGER NOT NULL DEFAULT 1,
            is_healthy INTEGER NOT NULL DEFAULT 1,
            score REAL NOT NULL DEFAULT 0.0,
            last_checked_at TEXT,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(provider, model_name)
        );

        CREATE TABLE IF NOT EXISTS request_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            provider TEXT,
            model_name TEXT,
            latency_ms INTEGER,
            success INTEGER NOT NULL DEFAULT 0,
            error_type TEXT,
            prompt_tokens INTEGER,
            completion_tokens INTEGER,
            total_tokens INTEGER
        );

        CREATE TABLE IF NOT EXISTS leaderboard_cache (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            model_name TEXT NOT NULL,
            source TEXT NOT NULL,
            score REAL NOT NULL,
            fetched_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(model_name, source)
        );

        CREATE TABLE IF NOT EXISTS config_overrides (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            key TEXT NOT NULL UNIQUE,
            value TEXT NOT NULL,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        """,
    )
]


@dataclass(slots=True)
class WriteTask:
    sql: str
    params: tuple[Any, ...] = ()


class Database:
    def __init__(self, path: str) -> None:
        self.path = path
        self._write_q: Queue[WriteTask] = Queue()
        self._stop = threading.Event()
        self._writer: threading.Thread | None = None

    def connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute("PRAGMA foreign_keys=ON;")
        return conn

    def migrate(self) -> None:
        conn = self.connect()
        try:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS schema_migrations (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    version TEXT NOT NULL UNIQUE,
                    applied_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
            for version, sql in MIGRATIONS:
                row = conn.execute(
                    "SELECT version FROM schema_migrations WHERE version = ?", (version,)
                ).fetchone()
                if row:
                    continue
                conn.executescript(sql)
                conn.execute("INSERT INTO schema_migrations(version) VALUES (?)", (version,))
            conn.commit()
        finally:
            conn.close()

    def start_writer(self) -> None:
        if self._writer and self._writer.is_alive():
            return
        self._stop.clear()
        self._writer = threading.Thread(target=self._writer_loop, daemon=True, name="db-writer")
        self._writer.start()

    def stop_writer(self) -> None:
        self.flush_writes()
        self._stop.set()
        if self._writer:
            self._writer.join(timeout=2)

    def enqueue(self, sql: str, params: tuple[Any, ...] = ()) -> None:
        self._write_q.put(WriteTask(sql=sql, params=params))

    def flush_writes(self) -> None:
        self._write_q.join()

    def _writer_loop(self) -> None:
        conn = self.connect()
        try:
            while not self._stop.is_set() or not self._write_q.empty():
                try:
                    task = self._write_q.get(timeout=0.2)
                except Empty:
                    continue
                try:
                    conn.execute(task.sql, task.params)
                    conn.commit()
                finally:
                    self._write_q.task_done()
        finally:
            conn.close()
