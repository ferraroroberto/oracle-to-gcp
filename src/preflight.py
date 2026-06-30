"""Go/no-go preflight checks for source/target table readiness."""

from __future__ import annotations

import sqlite3
from dataclasses import asdict, dataclass, field
from typing import Any

from src.pipeline_config import PipelineConfig
from src.sql_models import SqlUnit
from src.table_registry import (
    TableMapping,
    ensure_pending,
    get_mapping,
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
    mappings: list[TableMapping] = field(default_factory=list)
    messages: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-safe representation."""
        data = asdict(self)
        data["mappings"] = [asdict(mapping) for mapping in self.mappings]
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

    can_run = not unknown and not blocked
    if can_run:
        messages.append("All required table correspondences are mapped and reachable")
    return PreflightResult(
        can_run=can_run,
        required_tables=required,
        unknown_tables=unknown,
        blocked_tables=blocked,
        mappings=mappings,
        messages=messages,
    )


def _table_reachable(conn: sqlite3.Connection, table: str) -> bool:
    try:
        conn.execute(f"SELECT * FROM {table} LIMIT 1").fetchone()
    except sqlite3.Error:
        return False
    return True
