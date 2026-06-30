"""Unit tests for src/logger.py — namespacing + the in-memory ring buffer."""

from __future__ import annotations

import io
import logging
import sys

import pytest

from src import clear_log_buffer, get_log_buffer, get_logger
from src.logger import _configure_root


def test_get_logger_namespaces_under_app() -> None:
    assert get_logger("app").name == "app"
    assert get_logger("my_pipeline").name == "app.my_pipeline"


def test_ring_buffer_captures_then_clears() -> None:
    clear_log_buffer()
    get_logger("ring_test").info("hello-ring-buffer-marker")
    buffer = get_log_buffer()
    assert any("hello-ring-buffer-marker" in line for line in buffer)

    clear_log_buffer()
    assert get_log_buffer() == []


def test_debug_lines_stay_below_the_buffer_threshold() -> None:
    # The memory handler is INFO-level; DEBUG lines must not reach the buffer.
    clear_log_buffer()
    get_logger("ring_test").debug("debug-should-not-appear")
    assert not any("debug-should-not-appear" in line for line in get_log_buffer())


def test_console_handler_tolerates_emoji_on_cp1252_stdout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """StreamHandler must not raise UnicodeEncodeError on a cp1252 console."""
    # Simulate a cp1252 Windows console by wrapping a BytesIO with cp1252.
    buf = io.BytesIO()
    fake_stdout = io.TextIOWrapper(buf, encoding="cp1252", errors="strict")

    # Reset the idempotency flag so _configure_root() runs again with our stream.
    import src.logger as logger_mod
    original_configured = logger_mod._CONFIGURED
    logger_mod._CONFIGURED = False

    # Remove existing handlers so we can install a fresh StreamHandler.
    root = logging.getLogger("app")
    old_handlers = root.handlers[:]
    root.handlers.clear()

    try:
        monkeypatch.setattr(sys, "stdout", fake_stdout)
        _configure_root()

        # After reconfigure, sys.stdout should be UTF-8 (or at least not cp1252
        # strict), so logging an emoji must not raise.
        log = logging.getLogger("app.emoji_test")
        try:
            log.info("🚀 emoji test ✅")
        except UnicodeEncodeError as exc:
            raise AssertionError(
                "UnicodeEncodeError raised on emoji log — cp1252 console fix not working"
            ) from exc
    finally:
        # Restore everything so other tests are unaffected.
        root.handlers.clear()
        for h in old_handlers:
            root.addHandler(h)
        logger_mod._CONFIGURED = original_configured
