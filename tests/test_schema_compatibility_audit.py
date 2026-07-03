"""Tests for the standalone schema compatibility audit experiment."""

from __future__ import annotations

import csv
import json
import sqlite3
from pathlib import Path

from unit_test.schema_compatibility_audit import run_audit


def test_schema_audit_writes_column_and_table_reports(tmp_path: Path) -> None:
    """SQLite adapters should exercise the metadata comparison without live credentials."""
    oracle_db = tmp_path / "oracle.db"
    bigquery_db = tmp_path / "bigquery.db"
    with sqlite3.connect(oracle_db) as conn:
        conn.execute(
            """
            CREATE TABLE sales_orders (
                order_id INTEGER PRIMARY KEY,
                order_date DATE,
                amount NUMERIC,
                legacy_only TEXT
            )
            """
        )
        conn.execute("INSERT INTO sales_orders VALUES (1, '2026-07-01', 10.5, 'legacy')")
    with sqlite3.connect(bigquery_db) as conn:
        conn.execute(
            """
            CREATE TABLE raw_sales_orders (
                order_id INTEGER PRIMARY KEY,
                order_date TEXT,
                amount NUMERIC,
                target_only TEXT
            )
            """
        )
        conn.execute("INSERT INTO raw_sales_orders VALUES (1, '2026-07-01', 10.5, 'target')")

    correspondence_csv = tmp_path / "correspondence.csv"
    with correspondence_csv.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=[
                "oracle_schema",
                "oracle_table",
                "bigquery_project",
                "bigquery_dataset",
                "bigquery_table",
            ],
        )
        writer.writeheader()
        writer.writerow(
            {
                "oracle_schema": "DEMO",
                "oracle_table": "sales_orders",
                "bigquery_project": "mock-project",
                "bigquery_dataset": "mock_dataset",
                "bigquery_table": "raw_sales_orders",
            }
        )

    output_dir = tmp_path / "reports"
    config_path = tmp_path / "schema_audit_config.json"
    config_path.write_text(
        json.dumps(
            {
                "input": {"path": str(correspondence_csv), "format": "csv"},
                "oracle": {"mode": "sqlite", "db_path": str(oracle_db)},
                "bigquery": {"mode": "sqlite", "db_path": str(bigquery_db)},
                "output": {
                    "detail_csv": str(output_dir / "column_report.csv"),
                    "summary_csv": str(output_dir / "table_summary.csv"),
                    "detail_json": str(output_dir / "column_report.json"),
                    "summary_json": str(output_dir / "table_summary.json"),
                },
                "sampling": {"enabled": True, "max_rows": 2},
                "logging": {"level": "INFO"},
            }
        ),
        encoding="utf-8",
    )

    comparisons, summaries = run_audit(config_path)

    statuses = {row.oracle_column or row.bigquery_column: row.compatibility_status for row in comparisons}
    assert statuses["amount"] == "compatible"
    assert statuses["order_date"] == "type_mismatch"
    assert statuses["legacy_only"] == "missing_target"
    assert statuses["target_only"] == "extra_target"
    amount_row = next(row for row in comparisons if row.oracle_column == "amount")
    assert amount_row.oracle_sample_non_null == 1
    assert amount_row.bigquery_sample_non_null == 1
    assert summaries[0].status == "blocked"
    assert (output_dir / "column_report.csv").exists()
    assert (output_dir / "table_summary.json").exists()


def test_schema_audit_records_table_metadata_errors(tmp_path: Path) -> None:
    """One table failure should still produce a machine-readable error report."""
    correspondence_csv = tmp_path / "correspondence.csv"
    with correspondence_csv.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=[
                "oracle_schema",
                "oracle_table",
                "bigquery_project",
                "bigquery_dataset",
                "bigquery_table",
            ],
        )
        writer.writeheader()
        writer.writerow(
            {
                "oracle_schema": "DEMO",
                "oracle_table": "sales_orders",
                "bigquery_project": "mock-project",
                "bigquery_dataset": "mock_dataset",
                "bigquery_table": "raw_sales_orders",
            }
        )

    output_dir = tmp_path / "reports"
    config_path = tmp_path / "schema_audit_config.json"
    config_path.write_text(
        json.dumps(
            {
                "input": {"path": str(correspondence_csv), "format": "csv"},
                "oracle": {"mode": "sqlite", "db_path": str(tmp_path)},
                "bigquery": {"mode": "sqlite", "db_path": str(tmp_path / "bigquery.db")},
                "output": {
                    "detail_csv": str(output_dir / "column_report.csv"),
                    "summary_csv": str(output_dir / "table_summary.csv"),
                    "detail_json": str(output_dir / "column_report.json"),
                    "summary_json": str(output_dir / "table_summary.json"),
                },
            }
        ),
        encoding="utf-8",
    )

    comparisons, summaries = run_audit(config_path)

    assert comparisons[0].compatibility_status == "metadata_error"
    assert summaries[0].status == "blocked"
