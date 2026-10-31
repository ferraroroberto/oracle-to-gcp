"""The audit tools reach BigQuery only through ``src/bigquery_io.py``."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import pytest

from src import bigquery_io
from tests.audits.query_cost_audit import BigQueryDryRunEstimator
from tests.audits.query_optimization_loop import BigQueryQueryExecutor
from tests.audits.schema_compatibility_audit import BigQueryMetadataAdapter, ColumnMetadata, TablePair

REPO_ROOT = Path(__file__).resolve().parent.parent
BIGQUERY_IO = REPO_ROOT / "src" / "bigquery_io.py"
# semantic_layer/ is self-contained and must not import from src/, so it is out of scope here.
SCANNED_DIRS = ("src", "app", "tests/audits")
DIRECT_CLIENT_USE = re.compile(r"google\.cloud\s+import\s+bigquery|bigquery\.Client\(")


def test_only_bigquery_io_touches_the_bigquery_client_library() -> None:
    offenders = [
        f"{path.relative_to(REPO_ROOT).as_posix()}:{number}"
        for directory in SCANNED_DIRS
        for path in sorted((REPO_ROOT / directory).rglob("*.py"))
        if path != BIGQUERY_IO
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1)
        if DIRECT_CLIENT_USE.search(line)
    ]

    assert offenders == [], f"route BigQuery access through src/bigquery_io.py: {offenders}"


def test_cost_estimator_delegates_to_dry_run_helper(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[str, str | None]] = []
    monkeypatch.setattr(bigquery_io, "dry_run_bytes", lambda sql, project=None: calls.append((sql, project)) or 7)

    assert BigQueryDryRunEstimator({"project_id": "proj"}).estimate_bytes("SELECT 1") == 7
    assert BigQueryDryRunEstimator({}).estimate_bytes("SELECT 2") == 7
    assert calls == [("SELECT 1", "proj"), ("SELECT 2", None)]


def test_query_executor_delegates_to_run_query(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict[str, Any] = {}

    def fake_run_query(sql: str, project: str | None = None, params: dict[str, Any] | None = None) -> list[dict]:
        seen.update(sql=sql, project=project, params=params)
        return [{"a": 1}]

    monkeypatch.setattr(bigquery_io, "run_query", fake_run_query)

    assert BigQueryQueryExecutor({"project_id": "proj"}).execute("SELECT a") == [{"a": 1}]
    assert seen == {"sql": "SELECT a", "project": "proj", "params": None}


def test_metadata_adapter_reads_columns_and_samples_through_run_query(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[str, str | None, dict[str, Any] | None]] = []

    def fake_run_query(sql: str, project: str | None = None, params: dict[str, Any] | None = None) -> list[dict]:
        calls.append((sql, project, params))
        if "INFORMATION_SCHEMA" in sql:
            return [
                {"column_name": "id", "data_type": "INT64", "is_nullable": "NO", "ordinal_position": 1},
                {"column_name": "name", "data_type": "STRING", "is_nullable": "YES", "ordinal_position": 2},
            ]
        return [{"id": 1, "name": "a"}, {"id": 2, "name": None}]

    monkeypatch.setattr(bigquery_io, "run_query", fake_run_query)
    pair = TablePair("DEMO", "orders", "proj", "ds", "orders")
    adapter = BigQueryMetadataAdapter({})

    columns = adapter.columns_for(pair, "bigquery")
    samples = adapter.sample_for(pair, "bigquery", columns, 5)

    assert [(c.name, c.nullable, c.ordinal) for c in columns] == [("id", False, 1), ("name", True, 2)]
    assert isinstance(columns[0], ColumnMetadata)
    assert set(samples) == {"id", "name"}
    assert calls[0][1:] == ("proj", {"table_name": "orders"})
    assert calls[1][1:] == ("proj", {"row_limit": 5})
