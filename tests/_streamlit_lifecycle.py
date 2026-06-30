"""Streamlit lifecycle helper for the e2e regression suite.

A regression suite must run against the **current code on disk**. A Streamlit
process started earlier keeps serving stale modules even after an edit, so the
e2e session fixture force-restarts it: kill whatever holds the port, boot a
fresh ``streamlit run``, wait on ``/_stcore/health``, return the base URL.

Engine note: this is the *Streamlit instance* of the boot-or-adopt harness
rule in ``CLAUDE.md`` ("End-to-end UI testing"). A FastAPI / Flask project
swaps the launch command and the health path; the kill-port / wait-health
shape is identical.
"""

from __future__ import annotations

import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, UTC
from pathlib import Path
from collections.abc import Callable

_ROOT = Path(__file__).resolve().parent.parent
_APP = _ROOT / "app" / "app.py"
_MARKER = Path(__file__).resolve().parent / "_streamlit_restart_marker.txt"

# Per-project e2e port. Change this when scaffolding a new project so two
# scaffolded projects running their suites don't fight over the same port.
STREAMLIT_E2E_PORT = 8786

_HEALTH_TIMEOUT = 30.0


def port_is_in_use(port: int) -> bool:
    """True if something accepts TCP connections on 127.0.0.1:port."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.3)
        return sock.connect_ex(("127.0.0.1", port)) == 0


def _listening_pids(port: int) -> list[str]:
    """PIDs LISTENing on ``port`` (Windows netstat / POSIX lsof)."""
    if sys.platform == "win32":
        out = subprocess.run(
            ["netstat", "-ano", "-p", "TCP"],
            capture_output=True, text=True, check=False,
        ).stdout
        pids = set()
        for line in out.splitlines():
            parts = line.split()
            if (
                len(parts) >= 5
                and parts[3] == "LISTENING"
                and parts[1].endswith(f":{port}")
            ):
                pids.add(parts[4])
        return sorted(pids)
    out = subprocess.run(
        ["lsof", "-ti", f"tcp:{port}", "-sTCP:LISTEN"],
        capture_output=True, text=True, check=False,
    ).stdout
    return [pid for pid in out.split() if pid]


def kill_streamlit_on_port(port: int) -> None:
    """Force-kill whatever process is LISTENing on ``port`` (best effort)."""
    for pid in _listening_pids(port):
        try:
            if sys.platform == "win32":
                subprocess.run(
                    ["taskkill", "/F", "/PID", pid],
                    capture_output=True, check=False,
                )
            else:
                subprocess.run(["kill", "-9", pid], capture_output=True, check=False)
        except OSError:
            pass


def _wait_until(predicate: Callable[[], bool], timeout: float) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(0.4)
    return False


def _health_ok(base_url: str) -> bool:
    try:
        with urllib.request.urlopen(f"{base_url}/_stcore/health", timeout=2) as resp:
            return resp.status == 200
    except (urllib.error.URLError, OSError):
        return False


def _write_marker(port: int, pid: int) -> None:
    """Record the restart so a human can confirm which process is serving."""
    stamp = datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S UTC")
    line = f"streamlit e2e: pid={pid} port={port} restarted={stamp}"
    try:
        _MARKER.write_text(line + "\n", encoding="utf-8")
    except OSError:
        pass
    print(f"[e2e] {line}")


def ensure_fresh_streamlit(port: int = STREAMLIT_E2E_PORT) -> str:
    """Force a fresh ``streamlit run`` on ``port``; return its base URL.

    Kills any process already on the port, boots Streamlit headless against
    the current code, and waits for ``/_stcore/health``. Raises ``RuntimeError``
    if it doesn't come up — never returns a URL pointing at a stale process.
    """
    if port_is_in_use(port):
        kill_streamlit_on_port(port)
        _wait_until(lambda: not port_is_in_use(port), timeout=5.0)

    proc = subprocess.Popen(
        [
            sys.executable, "-m", "streamlit", "run", str(_APP),
            "--server.headless", "true",
            "--server.port", str(port),
            "--browser.gatherUsageStats", "false",
        ],
        cwd=str(_ROOT),
    )
    base_url = f"http://localhost:{port}"
    if not _wait_until(lambda: _health_ok(base_url), timeout=_HEALTH_TIMEOUT):
        proc.terminate()
        raise RuntimeError(
            f"streamlit did not pass /_stcore/health on :{port} within "
            f"{_HEALTH_TIMEOUT:.0f}s — boot failed (not a stale-cache issue)"
        )
    _write_marker(port, proc.pid)
    return base_url
