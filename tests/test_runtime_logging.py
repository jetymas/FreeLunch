from __future__ import annotations

import io
import json
import logging
import queue
import sys

from src.runtime_logging import (
    JsonLineFormatter,
    RuntimeLoggingManager,
    configure_runtime_logging,
    get_logger,
    get_runtime_logging_status,
    runtime_log,
    shutdown_runtime_logging,
)


def _parse_lines(buffer: io.StringIO) -> list[dict[str, object]]:
    lines = [line for line in buffer.getvalue().splitlines() if line.strip()]
    return [json.loads(line) for line in lines]


def test_runtime_logging_concise_filters_verbose_and_debug_events():
    stream = io.StringIO()
    configure_runtime_logging(
        enabled=True,
        verbosity="concise",
        queue_size=16,
        stream=stream,
    )
    logger = get_logger("tests.runtime")

    runtime_log(logger, "concise.event", verbosity="concise", message="concise")
    runtime_log(logger, "verbose.event", verbosity="verbose", message="verbose")
    runtime_log(logger, "debug.event", verbosity="debug", message="debug")
    shutdown_runtime_logging()

    events = [entry["event"] for entry in _parse_lines(stream)]
    assert events == ["concise.event"]


def test_runtime_logging_verbose_includes_concise_and_verbose():
    stream = io.StringIO()
    configure_runtime_logging(
        enabled=True,
        verbosity="verbose",
        queue_size=16,
        stream=stream,
    )
    logger = get_logger("tests.runtime")

    runtime_log(logger, "concise.event", verbosity="concise", message="concise")
    runtime_log(logger, "verbose.event", verbosity="verbose", message="verbose")
    runtime_log(logger, "debug.event", verbosity="debug", message="debug")
    shutdown_runtime_logging()

    events = [entry["event"] for entry in _parse_lines(stream)]
    assert events == ["concise.event", "verbose.event"]


def test_runtime_logging_debug_includes_all_events():
    stream = io.StringIO()
    configure_runtime_logging(
        enabled=True,
        verbosity="debug",
        queue_size=16,
        stream=stream,
    )
    logger = get_logger("tests.runtime")

    runtime_log(logger, "concise.event", verbosity="concise", message="concise", foo="bar")
    runtime_log(logger, "verbose.event", verbosity="verbose", message="verbose")
    runtime_log(logger, "debug.event", verbosity="debug", message="debug")
    shutdown_runtime_logging()

    entries = _parse_lines(stream)
    assert [entry["event"] for entry in entries] == [
        "concise.event",
        "verbose.event",
        "debug.event",
    ]
    assert entries[0]["foo"] == "bar"


def test_runtime_logging_overflow_drops_low_priority_and_keeps_status():
    stream = io.StringIO()
    configure_runtime_logging(
        enabled=True,
        verbosity="debug",
        queue_size=1,
        stream=stream,
    )
    logger = get_logger("tests.runtime")

    for index in range(100):
        runtime_log(
            logger,
            f"debug.event.{index}",
            verbosity="debug",
            level=logging.DEBUG,
            message="debug",
            index=index,
        )

    shutdown_runtime_logging()

    status = get_runtime_logging_status()
    assert status["dropped_records"] >= 0


def test_runtime_logging_status_reflects_configuration():
    stream = io.StringIO()
    configure_runtime_logging(
        enabled=True,
        verbosity="verbose",
        queue_size=25,
        stream=stream,
    )

    status = get_runtime_logging_status()
    shutdown_runtime_logging()

    assert status["enabled"] is True
    assert status["verbosity"] == "verbose"
    assert status["queue_size"] == 25


def test_runtime_logging_manager_disabled_mode_never_emits():
    manager = RuntimeLoggingManager()
    manager.configure(enabled=False, verbosity="verbose", queue_size=0, stream=io.StringIO())

    assert manager.should_emit(verbosity="concise", level=logging.INFO) is False
    assert manager.status()["enabled"] is False
    manager.stop()


def test_runtime_logging_manager_emits_warnings_even_when_verbosity_is_low():
    manager = RuntimeLoggingManager()
    manager.configure(enabled=True, verbosity="concise", queue_size=4, stream=io.StringIO())

    assert manager.should_emit(verbosity="debug", level=logging.WARNING) is True
    manager.stop()


def test_runtime_logging_includes_exception_and_keeps_prefixed_logger_name():
    formatter = JsonLineFormatter()
    logger = get_logger("freelunch.custom")
    assert logger.name == "freelunch.custom"

    try:
        raise RuntimeError("boom")
    except RuntimeError:
        payload = json.loads(
            formatter.format(
                logging.LogRecord(
                    name=logger.name,
                    level=logging.WARNING,
                    pathname=__file__,
                    lineno=1,
                    msg="probe failed",
                    args=(),
                    exc_info=sys.exc_info(),
                )
            )
        )
    assert payload["logger"] == "freelunch.custom"
    assert "RuntimeError: boom" in str(payload["exception"])


def test_runtime_logging_overflow_warning_uses_fallback_handler():
    class _Capture(logging.Handler):
        def __init__(self) -> None:
            super().__init__(level=logging.WARNING)
            self.messages: list[str] = []

        def emit(self, record: logging.LogRecord) -> None:
            self.messages.append(record.getMessage())

    manager = RuntimeLoggingManager()
    capture = _Capture()
    manager._fallback_handler = capture

    manager.handle_overflow_record(
        logging.LogRecord(
            name="freelunch.test",
            level=logging.WARNING,
            pathname=__file__,
            lineno=1,
            msg="overflow-warning",
            args=(),
            exc_info=None,
        )
    )

    assert manager.status()["dropped_records"] == 1
    assert capture.messages == ["overflow-warning"]


def test_runtime_logging_stop_handles_full_queue_when_queue_is_none():
    class _FakeListener:
        _thread = None

        def enqueue_sentinel(self) -> None:
            raise queue.Full()

    manager = RuntimeLoggingManager()
    manager._listener = _FakeListener()  # type: ignore[assignment]
    manager._queue = None
    manager.stop()

    assert manager.status()["enabled"] is False


def test_runtime_logging_stop_handles_full_queue_then_empty_queue():
    class _FakeListener:
        _thread = None

        def enqueue_sentinel(self) -> None:
            raise queue.Full()

    manager = RuntimeLoggingManager()
    manager._listener = _FakeListener()  # type: ignore[assignment]
    manager._queue = queue.Queue(maxsize=1)
    manager.stop()

    assert manager.status()["queue_depth"] == 0
