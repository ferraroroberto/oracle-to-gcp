"""Tests for connection config, redaction, and table preflight registry."""

from __future__ import annotations

import json

from src.mock_environment import bootstrap_mock_environment, connect_sqlite
from src.pipeline_config import config_to_dict, load_pipeline_config, write_pipeline_config
from src.pipelines.oracle_to_bigquery import run
from src.secrets import redact
from src.table_registry import export_csv, import_csv, list_mappings, template_csv


def test_config_roundtrip_includes_connection_sections_without_secret_values(tmp_path) -> None:
    config_path = tmp_path / "pipeline.json"
    raw = config_to_dict(load_pipeline_config())
    raw["oracle"]["username_env_var"] = "ORACLE_USER_FOR_TEST"
    raw["oracle"]["password_env_var"] = "ORACLE_PASSWORD_FOR_TEST"

    saved = write_pipeline_config(config_path, raw)
    loaded = load_pipeline_config(config_path)

    assert saved.oracle.password_env_var == "ORACLE_PASSWORD_FOR_TEST"
    assert loaded.google_cloud.project_id == "mock-gcp-project"
    serialized = json.dumps(config_to_dict(loaded))
    assert "actual-password" not in serialized


def test_redaction_masks_sensitive_keys() -> None:
    redacted = redact(
        {
            "password": "secret",
            "nested": {"api_key": "abc", "safe": "shown"},
            "items": [{"token": "xyz"}],
        }
    )

    assert redacted["password"] == "***REDACTED***"
    assert redacted["nested"]["api_key"] == "***REDACTED***"
    assert redacted["nested"]["safe"] == "shown"
    assert redacted["items"][0]["token"] == "***REDACTED***"


def test_registry_csv_template_import_export(tmp_path) -> None:
    registry = tmp_path / "registry.db"
    count = import_csv(template_csv(), registry)
    exported = export_csv(registry)

    assert count == 1
    assert "oracle_schema,oracle_table" in exported
    assert list_mappings(registry)[0].oracle_table == "sales_orders"


def test_unknown_table_preflight_blocks_and_inserts_pending_registry_row(tmp_path) -> None:
    config = load_pipeline_config()
    config.execution.table_registry_path = str(tmp_path / "table_registry.db")

    try:
        run(script="SELECT * FROM missing_table;", pipeline_config=config, use_local_hub=False, run_dir=tmp_path)
    except ValueError as exc:
        assert "table preflight failed" in str(exc)
    else:  # pragma: no cover - defensive
        raise AssertionError("preflight should block missing_table")

    pending = [row for row in list_mappings(config.execution.table_registry_path) if row.oracle_table == "missing_table"]
    assert pending
    assert pending[0].status == "pending"


def test_mock_preflight_marks_seeded_tables_reachable(tmp_path) -> None:
    report = run(use_local_hub=False, simulate_repair_path=False, run_dir=tmp_path)

    assert report.status == "validated"
    preflight_events = [event for event in report.trace if event["stage"] == "preflight"]
    assert preflight_events
    assert preflight_events[0]["details"]["can_run"] is True


def test_mock_table_probe_uses_limit_one(tmp_path) -> None:
    paths = bootstrap_mock_environment(tmp_path)
    with connect_sqlite(paths["oracle_db"]) as conn:
        assert conn.execute("SELECT * FROM sales_orders LIMIT 1").fetchone() is not None
