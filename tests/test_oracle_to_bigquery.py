"""End-to-end tests for the mock Oracle-to-BigQuery pipeline."""

from __future__ import annotations

from src.mock_environment import bootstrap_mock_environment, connect_sqlite, load_demo_script
from src.pipelines.oracle_to_bigquery import run
from src.sql_processing import build_units, materialize_variables


def test_materialize_variables_resolves_demo_run_date(tmp_path) -> None:
    paths = bootstrap_mock_environment(tmp_path)
    with connect_sqlite(paths["oracle_db"]) as conn:
        pure_sql, resolved = materialize_variables(load_demo_script(), conn)

    assert resolved == {"v_run_date": "DATE '2026-06-20'"}
    assert "v_run_date" not in pure_sql
    assert len(build_units(pure_sql)) == 2


def test_demo_pipeline_validates_and_repairs_with_mock_data(tmp_path) -> None:
    report = run(
        use_local_hub=False,
        simulate_repair_path=True,
        run_dir=tmp_path,
    )

    assert report.status == "validated"
    assert len(report.units) == 2
    assert report.units[0].repair_attempts == 1
    assert all(unit.status == "validated" for unit in report.units)
    assert "CREATE OR REPLACE TABLE scratch_daily_revenue AS" in report.final_bigquery_script
    assert "raw_sales_orders" in report.final_bigquery_script
