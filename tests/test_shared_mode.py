"""The share launcher (launch_server.*) serves a restricted Execution page.

A link from ``launch_server.bat`` / ``launch_server.sh`` reaches people who are
not the machine's owner, so in shared mode the Execution page offers only the
built-in demo: no config editing, no host paths, no registry writes.
"""

from __future__ import annotations

import importlib
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

import src.config
from app.views import translator_demo

ROOT = Path(__file__).resolve().parent.parent

# The Execution page imports pandas-heavy modules on first run; AppTest's 3 s
# default is too tight on a cold Windows interpreter.
_RENDER_TIMEOUT_S = 60


def _execution_page() -> None:
    from app.views import translator_demo

    translator_demo.render()


def _render(monkeypatch: pytest.MonkeyPatch, shared: bool) -> AppTest:
    monkeypatch.setattr(translator_demo, "SHARED_MODE", shared, raising=False)
    at = AppTest.from_function(_execution_page, default_timeout=_RENDER_TIMEOUT_S)
    at.run()
    assert not at.exception, at.exception
    return at


def test_local_launch_keeps_full_workspace(monkeypatch: pytest.MonkeyPatch) -> None:
    at = _render(monkeypatch, shared=False)

    assert len(at.tabs) == 5
    assert "pipeline_config_path" in {w.key for w in at.text_input}
    assert "config_json_editor" in {w.key for w in at.text_area}


def test_shared_launch_offers_only_the_builtin_demo(monkeypatch: pytest.MonkeyPatch) -> None:
    at = _render(monkeypatch, shared=True)

    assert len(at.tabs) == 0
    assert [w.key for w in at.text_input] == []
    assert [w.key for w in at.text_area] == []
    assert [w.key for w in at.selectbox] == []
    assert "demo_run_translation" in {w.key for w in at.button}
    assert {"save_pipeline_config", "test_connections", "execution_run_single"}.isdisjoint(
        {w.key for w in at.button}
    )


def test_shared_mode_flag_reads_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    try:
        monkeypatch.setenv("ORACLE_TO_GCP_SHARED_MODE", "1")
        assert importlib.reload(src.config).SHARED_MODE is True
        monkeypatch.delenv("ORACLE_TO_GCP_SHARED_MODE")
        assert importlib.reload(src.config).SHARED_MODE is False
    finally:
        monkeypatch.undo()
        importlib.reload(src.config)


@pytest.mark.parametrize("launcher", ["launch_server.bat", "launch_server.sh"])
def test_share_launchers_enable_shared_mode(launcher: str) -> None:
    text = (ROOT / launcher).read_text(encoding="utf-8")
    assert "ORACLE_TO_GCP_SHARED_MODE=1" in text
