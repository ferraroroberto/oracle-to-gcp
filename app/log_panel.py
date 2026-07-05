"""Streamlit-side log panel helpers.

Paints the in-memory ring buffer maintained by ``src.logger`` into a live
(``stream_to_streamlit``) or one-shot (``render_log_panel``) panel. This is
UI code, so it lives here rather than in ``src/logger.py`` — non-UI code
must never import ``streamlit``.

Usage
-----
In a Streamlit page that runs a pipeline::

    from app.log_panel import stream_to_streamlit

    stream_to_streamlit(lambda: run_my_pipeline())

The pipeline runs on a background thread while the same log lines stream
live into a panel inside the page — and into the terminal.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from typing import Any

import streamlit as st

from src.logger import get_log_buffer


def stream_to_streamlit(
    work: Callable[[], Any],
    title: str = "Live log",
    poll_seconds: float = 0.25,
) -> Any:
    """Run ``work()`` while streaming its log output into a live panel.

    ``work`` is executed on a background thread so the calling (script-run)
    thread stays free to repaint the panel ~``1/poll_seconds`` times a second.
    Streamlit UI calls must happen on the script thread, so it is the *work*
    that moves off-thread, not the painting — the panel therefore scrolls live
    while ``work`` runs, then settles on the final buffer. This is the single
    canonical "stream logs into a live Streamlit panel" implementation; pages
    should call it rather than hand-rolling their own refresh loop.

    Usage::

        stream_to_streamlit(lambda: run_my_pipeline())

    Returns whatever ``work()`` returns. Any exception raised inside ``work``
    propagates out of this call after the final repaint, exactly as if it had
    run inline on the calling thread.
    """
    placeholder = st.empty()
    start_len = len(get_log_buffer())

    def _paint(empty_msg: str) -> None:
        lines = get_log_buffer()[start_len:]
        body = "\n".join(lines) if lines else empty_msg
        with placeholder.container():
            st.markdown(f"**{title}**")
            st.code(body, language="log")

    done = threading.Event()
    result: list[Any] = []
    error: list[BaseException] = []

    def _runner() -> None:
        try:
            result.append(work())
        except BaseException as exc:  # noqa: BLE001 — re-raised on the main thread
            error.append(exc)
        finally:
            done.set()

    worker = threading.Thread(target=_runner, daemon=True)
    _paint("(starting...)")
    worker.start()

    # Poll-and-paint on the script thread until the work finishes.
    while not done.is_set():
        _paint("(starting...)")
        time.sleep(poll_seconds)
    worker.join()

    _paint("(no output)")
    if error:
        raise error[0]
    return result[0]


def render_log_panel(
    title: str = "Recent log",
    tail: int = 200,
) -> None:
    """One-shot panel showing the last ``tail`` log lines."""
    lines = get_log_buffer()[-tail:]
    body = "\n".join(lines) if lines else "(no log entries yet)"
    st.markdown(f"**{title}**")
    st.code(body, language="log")
