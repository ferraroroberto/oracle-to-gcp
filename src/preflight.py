"""Go/no-go preflight checks for source/target table readiness."""

from __future__ import annotations

import sqlite3
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from typing import Any

from src.pipeline_config import PipelineConfig
from src.sql_models import SqlUnit
from src.table_registry import (
    ColumnMapping,
    TableMapping,
    ensure_pending,
    get_mapping,
    replace_column_mappings,
    seed_from_mapping_dict,
    update_reachability,
)


@dataclass(slots=True)
class PreflightResult:
    """Structured preflight outcome for reporting and UI."""

    can_run: bool
    required_tables: list[str]
    unknown_tables: list[str] = field(default_factory=list)
    blocked_tables: list[str] = field(default_factory=list)
    schema_blocked_tables: list[str] = field(default_factory=list)
    mappings: list[TableMapping] = field(default_factory=list)
    column_mappings: list[ColumnMapping] = field(default_factory=list)
    messages: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-safe representation."""
        data = asdict(self)
        data["mappings"] = [asdict(mapping) for mapping in self.mappings]
        data["column_mappings"] = [asdict(mapping) for mapping in self.column_mappings]
        return data


def run_table_preflight(
    *,
    units: list[SqlUnit],
    pipeline_config: PipelineConfig,
    legacy_mapping: dict[str, str],
    oracle_conn: sqlite3.Connection,
    bigquery_conn: sqlite3.Connection,
    registry_path: str | None = None,
) -> PreflightResult:
    """Ensure every external source table is mapped and reachable before translation."""
    seed_from_mapping_dict(
        legacy_mapping,
        oracle_schema=pipeline_config.oracle.default_schema,
        bigquery_project=pipeline_config.bigquery.project_id,
        bigquery_dataset=pipeline_config.bigquery.dataset,
        path=registry_path,
    )
    produced_targets = {target for unit in units for target in unit.targets}
    required = sorted({source for unit in units for source in unit.sources if source not in produced_targets})
    unknown: list[str] = []
    blocked: list[str] = []
    mappings: list[TableMapping] = []
    messages: list[str] = []
    schema = pipeline_config.oracle.default_schema.lower()

    for source in required:
        mapping = get_mapping(schema, source, registry_path)
        if mapping is None:
            mapping = ensure_pending(schema, source, registry_path)
            unknown.append(source)
            messages.append(f"{source}: added as pending because no BigQuery correspondence exists")
            mappings.append(mapping)
            continue

        if not mapping.bigquery_table:
            unknown.append(source)
            messages.append(f"{source}: pending BigQuery target")
            mappings.append(mapping)
            continue

        oracle_ok = mapping.oracle_reachable or _table_reachable(oracle_conn, source)
        bq_ok = mapping.bigquery_reachable or _table_reachable(bigquery_conn, mapping.bigquery_table)
        mapping = update_reachability(
            schema,
            source,
            oracle_reachable=oracle_ok,
            bigquery_reachable=bq_ok,
            path=registry_path,
        )
        mappings.append(mapping)
        if not mapping.ready:
            blocked.append(source)
            messages.append(
                f"{source}: reachability incomplete oracle={mapping.oracle_reachable} "
                f"bigquery={mapping.bigquery_reachable} status={mapping.status}"
            )

    column_mappings: list[ColumnMapping] = []
    schema_blocked: list[str] = []
    if not unknown and not blocked:
        for mapping in mappings:
            refreshed = refresh_schema_compatibility(
                mapping=mapping,
                oracle_conn=oracle_conn,
                bigquery_conn=bigquery_conn,
                registry_path=registry_path,
            )
            column_mappings.extend(refreshed)
            incompatible = [row for row in refreshed if row.compatibility_status != "compatible"]
            if incompatible:
                schema_blocked.append(mapping.oracle_table)
                messages.append(
                    f"{mapping.oracle_table}: schema compatibility failed for "
                    f"{len(incompatible)} column(s)"
                )

    can_run = not unknown and not blocked and not schema_blocked
    if can_run:
        messages.append("All required table correspondences are mapped, reachable, and schema-compatible")
    return PreflightResult(
        can_run=can_run,
        required_tables=required,
        unknown_tables=unknown,
        blocked_tables=blocked,
        schema_blocked_tables=schema_blocked,
        mappings=mappings,
        column_mappings=column_mappings,
        messages=messages,
    )


def refresh_schema_compatibility(
    *,
    mapping: TableMapping,
    oracle_conn: sqlite3.Connection,
    bigquery_conn: sqlite3.Connection,
    registry_path: str | None = None,
) -> list[ColumnMapping]:
    """Refresh and persist column-level compatibility for one ready table mapping."""
    checked_at = _now_iso()
    oracle_columns = _table_columns(oracle_conn, mapping.oracle_table)
    bigquery_columns = _table_columns(bigquery_conn, mapping.bigquery_table)
    all_columns = sorted(set(oracle_columns) | set(bigquery_columns))
    rows = [
        _column_mapping(mapping, column, oracle_columns.get(column), bigquery_columns.get(column), checked_at)
        for column in all_columns
    ]
    replace_column_mappings(mapping, rows, registry_path)
    return rows


def _column_mapping(
    mapping: TableMapping,
    column: str,
    oracle_type: str | None,
    bigquery_type: str | None,
    checked_at: str,
) -> ColumnMapping:
    oracle_present = oracle_type is not None
    bigquery_present = bigquery_type is not None
    if not oracle_present:
        status = "extra_target"
        notes = "Column exists only in BigQuery target"
    elif not bigquery_present:
        status = "missing_target"
        notes = "Column exists in Oracle source but is missing from BigQuery target"
    elif _types_compatible(oracle_type or "", bigquery_type or ""):
        status = "compatible"
        notes = ""
    else:
        status = "type_mismatch"
        notes = f"Oracle {oracle_type} is not compatible with BigQuery {bigquery_type}"
    return ColumnMapping(
        oracle_schema=mapping.oracle_schema,
        oracle_table=mapping.oracle_table,
        oracle_column=column,
        oracle_type=oracle_type or "",
        bigquery_project=mapping.bigquery_project,
        bigquery_dataset=mapping.bigquery_dataset,
        bigquery_table=mapping.bigquery_table,
        bigquery_column=column if bigquery_present else "",
        bigquery_type=bigquery_type or "",
        oracle_present=oracle_present,
        bigquery_present=bigquery_present,
        compatibility_status=status,
        checked_at=checked_at,
        notes=notes,
    )


def _table_columns(conn: sqlite3.Connection, table: str) -> dict[str, str]:
    try:
        rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    except sqlite3.Error:
        return {}
    return {str(row["name"]).lower(): str(row["type"] or "").upper() for row in rows}


def _types_compatible(oracle_type: str, bigquery_type: str) -> bool:
    return _type_family(oracle_type) == _type_family(bigquery_type)


def _type_family(raw_type: str) -> str:
    normalized = raw_type.upper()
    if any(token in normalized for token in ("INT", "NUM", "DEC", "REAL", "FLOAT", "DOUBLE")):
        return "numeric"
    if any(token in normalized for token in ("CHAR", "CLOB", "TEXT", "STRING")):
        return "text"
    if any(token in normalized for token in ("DATE", "TIME")):
        return "temporal"
    if "BOOL" in normalized:
        return "boolean"
    return normalized or "unknown"


def _now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _table_reachable(conn: sqlite3.Connection, table: str) -> bool:
    try:
        conn.execute(f"SELECT * FROM {table} LIMIT 1").fetchone()
    except sqlite3.Error:
        return False
    return True
