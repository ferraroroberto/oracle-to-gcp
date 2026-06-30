"""Execution and fingerprint validation for the mock dual-engine run."""

from __future__ import annotations

import math
import sqlite3
from typing import Any

from src.sql_processing import (
    bigquery_sqlite_runtime_sql,
    extract_targets,
    oracle_sqlite_runtime_sql,
)


def assert_rowcount_parity(
    oracle_conn: sqlite3.Connection,
    bigquery_conn: sqlite3.Connection,
    mapping: dict[str, str],
    sources: list[str],
) -> list[dict[str, Any]]:
    """Verify mapped source inputs have matching row counts."""
    checks: list[dict[str, Any]] = []
    for source in sources:
        target = mapping.get(source)
        if target is None:
            continue
        oracle_count = oracle_conn.execute(f"SELECT COUNT(*) FROM {source}").fetchone()[0]
        bq_count = bigquery_conn.execute(f"SELECT COUNT(*) FROM {target}").fetchone()[0]
        checks.append({"source": source, "target": target, "oracle": oracle_count, "bigquery": bq_count})
        if oracle_count != bq_count:
            raise ValueError(f"row-count mismatch for {source}: oracle={oracle_count}, bigquery={bq_count}")
    return checks


def execute_oracle_unit(conn: sqlite3.Connection, sql: str) -> list[dict[str, Any]]:
    """Execute an Oracle-ish unit on the SQLite Oracle mock."""
    runtime_sql = oracle_sqlite_runtime_sql(sql)
    for target in extract_targets(runtime_sql):
        conn.execute(f"DROP TABLE IF EXISTS {target}")
    cursor = conn.execute(runtime_sql)
    conn.commit()
    if cursor.description:
        return _rows(cursor)
    targets = extract_targets(runtime_sql)
    if targets:
        return _table_rows(conn, targets[0])
    return []


def execute_bigquery_unit(conn: sqlite3.Connection, sql: str) -> list[dict[str, Any]]:
    """Execute a BigQuery-ish unit on the SQLite BigQuery mock."""
    rows: list[dict[str, Any]] = []
    for statement in bigquery_sqlite_runtime_sql(sql):
        cursor = conn.execute(statement)
        if cursor.description:
            rows = _rows(cursor)
    conn.commit()
    targets = extract_targets(sql)
    if targets:
        return _table_rows(conn, targets[0])
    return rows


def compare_fingerprints(
    oracle_rows: list[dict[str, Any]],
    bigquery_rows: list[dict[str, Any]],
    *,
    tolerance: float = 0.0001,
) -> dict[str, Any]:
    """Compare cheap statistical fingerprints for two result sets."""
    oracle_fp = fingerprint_rows(oracle_rows)
    bq_fp = fingerprint_rows(bigquery_rows)
    diffs: list[str] = []

    if oracle_fp["count"] != bq_fp["count"]:
        diffs.append(f"count oracle={oracle_fp['count']} bigquery={bq_fp['count']}")

    for column, oracle_sum in oracle_fp["numeric_sums"].items():
        bq_sum = bq_fp["numeric_sums"].get(column)
        if bq_sum is None or not math.isclose(oracle_sum, bq_sum, rel_tol=tolerance, abs_tol=tolerance):
            diffs.append(f"sum({column}) oracle={oracle_sum} bigquery={bq_sum}")

    if oracle_fp["group_fingerprint"] != bq_fp["group_fingerprint"]:
        diffs.append("group fingerprint mismatch")

    return {
        "matched": not diffs,
        "diffs": diffs,
        "oracle": oracle_fp,
        "bigquery": bq_fp,
    }


def fingerprint_rows(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Build count, numeric sums, and grouped count/sum fingerprints."""
    if not rows:
        return {"count": 0, "numeric_sums": {}, "group_fingerprint": {}}

    columns = list(rows[0])
    numeric_columns = [
        column
        for column in columns
        if any(isinstance(row[column], int | float) and row[column] is not None for row in rows)
    ]
    key_column = next((column for column in columns if column not in numeric_columns), columns[0])
    numeric_sums = {
        column: round(sum(float(row[column] or 0) for row in rows), 6)
        for column in numeric_columns
    }

    grouped: dict[str, dict[str, Any]] = {}
    for row in rows:
        key = str(row[key_column])
        group = grouped.setdefault(key, {"count": 0, "sums": {column: 0.0 for column in numeric_columns}})
        group["count"] += 1
        for column in numeric_columns:
            group["sums"][column] = round(group["sums"][column] + float(row[column] or 0), 6)

    return {
        "count": len(rows),
        "numeric_sums": numeric_sums,
        "group_fingerprint": grouped,
    }


def _table_rows(conn: sqlite3.Connection, table: str) -> list[dict[str, Any]]:
    cursor = conn.execute(f"SELECT * FROM {table}")
    return _rows(cursor)


def _rows(cursor: sqlite3.Cursor) -> list[dict[str, Any]]:
    return [dict(row) for row in cursor.fetchall()]
