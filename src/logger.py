"""
Elegant Logger
==============
A single logger that writes simultaneously to:

* The terminal (color-coded, timestamped, rich-style).
* A rotating file under ``data/logs/``.
* An in-memory ring buffer, read by ``app.log_panel``'s Streamlit helpers
  (``stream_to_streamlit()`` / ``render_log_panel()``) to paint a live panel.

Usage
-----
    from src import get_logger

    log = get_logger("my_pipeline")
    log.info("Starting work...")
    log.warning("Something looks off")
    log.error("Boom")

In a Streamlit page that runs a pipeline, use ``app.log_panel``'s
``stream_to_streamlit(lambda: run_my_pipeline())`` — this module stays
UI-free so non-UI code never imports ``streamlit``.
"""

from __future__ import annotations

import logging
import sys
import threading
from logging.handlers import RotatingFileHandler
from pathlib import Path

# ---------------------------------------------------------------------------
# Paths & constants
# ---------------------------------------------------------------------------
_LOG_DIR = Path(__file__).resolve().parent.parent / "data" / "logs"
_LOG_DIR.mkdir(parents=True, exist_ok=True)
_LOG_FILE = _LOG_DIR / "app.log"

_FMT = "%(asctime)s  %(levelname)-7s  %(name)-22s  %(message)s"
_DATEFMT = "%H:%M:%S"

# ANSI color codes — kept here so we don't hard-depend on `rich`.
_COLORS = {
    "DEBUG":    "\033[2;37m",   # dim white
    "INFO":     "\033[36m",     # cyan
    "WARNING":  "\033[33m",     # yellow
    "ERROR":    "\033[31m",     # red
    "CRITICAL": "\033[1;41;97m",  # white on red, bold
}
_RESET = "\033[0m"


class _ColorFormatter(logging.Formatter):
    """Adds ANSI colors per log level."""

    def format(self, record: logging.LogRecord) -> str:  # noqa: D401
        color = _COLORS.get(record.levelname, "")
        base = super().format(record)
        if not color:
            return base
        return f"{color}{base}{_RESET}"


# ---------------------------------------------------------------------------
# In-memory ring buffer + Streamlit handler
# ---------------------------------------------------------------------------
_BUFFER_LOCK = threading.Lock()
_RING_BUFFER: list[str] = []
_RING_MAX = 2000  # keep the last N lines in memory


class _MemoryHandler(logging.Handler):
    """Stores formatted lines in a process-wide ring buffer."""

    def emit(self, record: logging.LogRecord) -> None:  # noqa: D401
        msg = self.format(record)
        with _BUFFER_LOCK:
            _RING_BUFFER.append(msg)
            if len(_RING_BUFFER) > _RING_MAX:
                del _RING_BUFFER[: len(_RING_BUFFER) - _RING_MAX]


def get_log_buffer() -> list[str]:
    """Return a copy of the in-memory log lines (newest last)."""
    with _BUFFER_LOCK:
        return list(_RING_BUFFER)


def clear_log_buffer() -> None:
    """Wipe the in-memory buffer (does not touch the file)."""
    with _BUFFER_LOCK:
        _RING_BUFFER.clear()


# ---------------------------------------------------------------------------
# Root logger configuration (idempotent)
# ---------------------------------------------------------------------------
_CONFIGURED = False


def _configure_root() -> None:
    global _CONFIGURED
    if _CONFIGURED:
        return

    # On Windows the default console encoding is cp1252; emoji in log messages
    # would raise UnicodeEncodeError inside the StreamHandler and flood the
    # console with "--- Logging error ---" tracebacks instead of log lines.
    # Reconfigure both streams to UTF-8 with replacement before attaching the
    # handler, so non-encodable glyphs degrade gracefully.
    for _stream_name in ("stdout", "stderr"):
        _s = getattr(sys, _stream_name, None)
        if _s is not None and hasattr(_s, "reconfigure"):
            try:
                _s.reconfigure(encoding="utf-8", errors="replace")
            except Exception:
                pass

    root = logging.getLogger("app")
    root.setLevel(logging.DEBUG)
    root.propagate = False

    stream = logging.StreamHandler(sys.stdout)
    stream.setLevel(logging.INFO)
    stream.setFormatter(_ColorFormatter(_FMT, _DATEFMT))
    root.addHandler(stream)

    file_h = RotatingFileHandler(
        _LOG_FILE, maxBytes=1_000_000, backupCount=3, encoding="utf-8"
    )
    file_h.setLevel(logging.DEBUG)
    file_h.setFormatter(logging.Formatter(_FMT, _DATEFMT))
    root.addHandler(file_h)

    mem = _MemoryHandler()
    mem.setLevel(logging.INFO)
    mem.setFormatter(logging.Formatter(_FMT, _DATEFMT))
    root.addHandler(mem)

    _CONFIGURED = True


def get_logger(name: str = "app") -> logging.Logger:
    """Return a namespaced logger under the ``app.*`` tree."""
    _configure_root()
    return logging.getLogger(f"app.{name}" if name != "app" else "app")
