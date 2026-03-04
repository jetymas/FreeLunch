from __future__ import annotations

import io
import json
import logging

from src.runtime_logging import (
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
