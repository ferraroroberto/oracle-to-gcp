"""Headless render checks for the Execution page's configuration tabs."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

from src.pipeline_config import DEFAULT_PIPELINE_CONFIG_PATH
from src.table_registry import TableMapping, list_mappings, upsert_mapping


def _render_page() -> None:
    from app.views import translator_demo

    translator_demo.render()


def _config_file(tmp_path: Path, *, gcp_auth_mode: str, registry: Path) -> Path:
    raw = json.loads(DEFAULT_PIPELINE_CONFIG_PATH.read_text(encoding="utf-8"))
    raw["google_cloud"]["auth_mode"] = gcp_auth_mode
    raw["execution"]["table_registry_path"] = str(registry)
    path = tmp_path / "pipeline.json"
    path.write_text(json.dumps(raw), encoding="utf-8")
    return path


def _open_page(config_path: Path) -> AppTest:
    app = AppTest.from_function(_render_page, default_timeout=30)
    app.session_state["pipeline_config_path"] = str(config_path)
    return app.run()


@pytest.mark.parametrize("auth_mode", ["application_default", "oauth", "service_account"])
def test_gcp_auth_mode_selectbox_shows_the_configured_value(tmp_path: Path, auth_mode: str) -> None:
    app = _open_page(_config_file(tmp_path, gcp_auth_mode=auth_mode, registry=tmp_path / "registry.db"))

    assert not app.exception
    assert app.selectbox(key="cfg_gcp_auth").value == auth_mode


def test_table_correspondence_tab_reads_the_configured_registry(tmp_path: Path) -> None:
    registry = tmp_path / "configured_registry.db"
    upsert_mapping(TableMapping(oracle_schema="demo", oracle_table="orders", bigquery_table="orders"), registry)

    app = _open_page(_config_file(tmp_path, gcp_auth_mode="mock", registry=registry))

    assert not app.exception
    shown = [frame.value.to_dict("records") for frame in app.dataframe]
    assert any(row.get("oracle_table") == "orders" for rows in shown for row in rows)
    assert [m.oracle_table for m in list_mappings(registry)] == ["orders"]


def test_second_demo_run_is_refused_while_one_is_in_flight(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from app.views import translator_demo

    started: list[str] = []
    monkeypatch.setattr(translator_demo, "_run_demo_translation", lambda *args: started.append("run"))
    app = _open_page(_config_file(tmp_path, gcp_auth_mode="mock", registry=tmp_path / "registry.db"))
    lock = translator_demo._demo_run_lock()

    assert lock.acquire(blocking=False)
    try:
        app.button(key="demo_run_translation").click().run()
        assert [w.value for w in app.warning] == ["Another demo run is in progress. Wait for it to finish, then run again."]
        assert started == []
    finally:
        lock.release()

    app.button(key="demo_run_translation").click().run()
    assert started == ["run"]
    assert not app.warning
    assert lock.acquire(blocking=False), "the lock must be released after a run"
    lock.release()
