from __future__ import annotations

import json
import logging
import queue
import sys
import threading
from datetime import datetime, timezone
from logging.handlers import QueueHandler, QueueListener
from typing import Any, TextIO

RUNTIME_VERBOSITY_ORDER = {
    "concise": 0,
    "verbose": 1,
    "debug": 2,
}
_BASE_LOGGER_NAME = "freelunch"
_DEFAULT_RECORD_ATTRS = frozenset(logging.makeLogRecord({}).__dict__.keys())


def normalize_runtime_verbosity(value: Any) -> str:
    normalized = str(value or "").strip().lower()
    return normalized if normalized in RUNTIME_VERBOSITY_ORDER else "concise"


class JsonLineFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": (
                datetime.now(timezone.utc)
                .replace(microsecond=0)
                .isoformat()
                .replace("+00:00", "Z")
            ),
            "level": record.levelname.lower(),
            "logger": record.name,
            "event": getattr(record, "event", record.name),
            "message": record.getMessage(),
        }
        verbosity = getattr(record, "verbosity", None)
        if verbosity is not None:
            payload["verbosity"] = verbosity

        for key, value in record.__dict__.items():
            if key in _DEFAULT_RECORD_ATTRS or key in {"event", "verbosity", "message"}:
                continue
            payload[key] = value

        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=True, default=str)


class RuntimeLoggingManager:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._queue: queue.Queue[logging.LogRecord] | None = None
        self._listener: QueueListener | None = None
        self._queue_handler: QueueHandler | None = None
        self._fallback_handler: logging.Handler | None = None
        self._enabled = False
        self._verbosity = "concise"
        self._queue_size = 1000
        self._dropped_records = 0
        self._stream: TextIO = sys.stdout

    def configure(
        self,
        *,
        enabled: bool,
        verbosity: str,
        queue_size: int,
        stream: TextIO | None = None,
    ) -> None:
        normalized_verbosity = normalize_runtime_verbosity(verbosity)
        normalized_queue_size = max(int(queue_size), 1)
        target_stream = stream or sys.stdout

        with self._lock:
            self._stop_locked()
            self._enabled = enabled
            self._verbosity = normalized_verbosity
            self._queue_size = normalized_queue_size
            self._stream = target_stream
            self._dropped_records = 0

            base_logger = logging.getLogger(_BASE_LOGGER_NAME)
            base_logger.setLevel(logging.DEBUG)
            base_logger.handlers.clear()
            base_logger.propagate = False

            if not enabled:
                return

            log_queue: queue.Queue[logging.LogRecord] = queue.Queue(maxsize=normalized_queue_size)
            formatter = JsonLineFormatter()
            stream_handler = logging.StreamHandler(target_stream)
            stream_handler.setFormatter(formatter)
            stream_handler.setLevel(logging.DEBUG)

            fallback_handler = logging.StreamHandler(target_stream)
            fallback_handler.setFormatter(formatter)
            fallback_handler.setLevel(logging.WARNING)

            queue_handler = _DroppingQueueHandler(log_queue, self)
            queue_handler.setLevel(logging.DEBUG)

            listener = QueueListener(log_queue, stream_handler, respect_handler_level=True)
            listener.start()

            base_logger.addHandler(queue_handler)
            self._queue = log_queue
            self._listener = listener
            self._queue_handler = queue_handler
            self._fallback_handler = fallback_handler

    def stop(self) -> None:
        with self._lock:
            self._stop_locked()
            self._enabled = False

    def _stop_locked(self) -> None:
        if self._listener is not None:
            while True:
                try:
                    self._listener.enqueue_sentinel()
                    break
                except queue.Full:
                    if self._queue is None:
                        break
                    try:
                        self._queue.get_nowait()
                    except queue.Empty:
                        break
            listener_thread = self._listener._thread
            if listener_thread is not None:
                listener_thread.join()
        self._listener = None
        self._queue = None
        self._queue_handler = None
        self._fallback_handler = None
        logging.getLogger(_BASE_LOGGER_NAME).handlers.clear()

    def should_emit(self, *, verbosity: str, level: int) -> bool:
        normalized_verbosity = normalize_runtime_verbosity(verbosity)
        with self._lock:
            if not self._enabled:
                return False
            if level >= logging.WARNING:
                return True
            current_order = RUNTIME_VERBOSITY_ORDER[self._verbosity]
        return RUNTIME_VERBOSITY_ORDER[normalized_verbosity] <= current_order

    def record_drop(self) -> None:
        with self._lock:
            self._dropped_records += 1

    def handle_overflow_record(self, record: logging.LogRecord) -> None:
        self.record_drop()
        if record.levelno < logging.WARNING:
            return
        with self._lock:
            handler = self._fallback_handler
        if handler is not None:
            handler.handle(record)

    def status(self) -> dict[str, Any]:
        with self._lock:
            queue_depth = self._queue.qsize() if self._queue is not None else 0
            return {
                "enabled": self._enabled,
                "verbosity": self._verbosity,
                "queue_size": self._queue_size,
                "queue_depth": queue_depth,
                "dropped_records": self._dropped_records,
            }


class _DroppingQueueHandler(QueueHandler):
    def __init__(
        self,
        log_queue: queue.Queue[logging.LogRecord],
        manager: RuntimeLoggingManager,
    ) -> None:
        super().__init__(log_queue)
        self._manager = manager

    def enqueue(self, record: logging.LogRecord) -> None:
        try:
            self.queue.put_nowait(record)
        except queue.Full:
            self._manager.handle_overflow_record(record)


_MANAGER = RuntimeLoggingManager()


def configure_runtime_logging(
    *,
    enabled: bool,
    verbosity: str,
    queue_size: int,
    stream: TextIO | None = None,
) -> None:
    _MANAGER.configure(
        enabled=enabled,
        verbosity=verbosity,
        queue_size=queue_size,
        stream=stream,
    )


def shutdown_runtime_logging() -> None:
    _MANAGER.stop()


def get_runtime_logging_status() -> dict[str, Any]:
    return _MANAGER.status()


def get_logger(name: str) -> logging.Logger:
    normalized_name = str(name or "").strip() or "app"
    if normalized_name.startswith(_BASE_LOGGER_NAME):
        return logging.getLogger(normalized_name)
    normalized_name = normalized_name.removeprefix("src.")
    return logging.getLogger(f"{_BASE_LOGGER_NAME}.{normalized_name}")


def runtime_log(
    logger: logging.Logger,
    event: str,
    *,
    verbosity: str = "concise",
    level: int = logging.INFO,
    message: str | None = None,
    exc_info: Any = None,
    **fields: Any,
) -> None:
    if not _MANAGER.should_emit(verbosity=verbosity, level=level):
        return
    logger.log(
        level,
        message or event,
        extra={
            "event": event,
            "verbosity": normalize_runtime_verbosity(verbosity),
            **fields,
        },
        exc_info=exc_info,
    )
