"""Durable Oracle-to-BigQuery table correspondence registry."""

from __future__ import annotations

import csv
import io
import sqlite3
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from src.config import ROOT_DIR

DEFAULT_REGISTRY_PATH = ROOT_DIR / "data" / "table_registry.db"
CSV_COLUMNS = [
    "oracle_schema",
    "oracle_table",
    "bigquery_project",
    "bigquery_dataset",
    "bigquery_table",
    "status",
    "oracle_reachable",
    "oracle_checked_at",
    "bigquery_reachable",
    "bigquery_checked_at",
    "notes",
]


@dataclass(slots=True)
class TableMapping:
    """One source-target table correspondence row."""

    oracle_schema: str
    oracle_table: str
    bigquery_project: str = ""
    bigquery_dataset: str = ""
    bigquery_table: str = ""
    status: str = "pending"
    oracle_reachable: bool = False
    oracle_checked_at: str = ""
    bigquery_reachable: bool = False
    bigquery_checked_at: str = ""
    notes: str = ""

    @property
    def ready(self) -> bool:
        """Return whether this row is complete enough for execution."""
        return (
            self.status == "ready"
            and bool(self.bigquery_table)
            and self.oracle_reachable
            and self.bigquery_reachable
        )


def registry_path(path: str | Path | None = None) -> Path:
    """Resolve the registry database path."""
    if path is None:
        return DEFAULT_REGISTRY_PATH
    resolved = Path(path)
    return resolved if resolved.is_absolute() else ROOT_DIR / resolved


def init_registry(path: str | Path | None = None) -> Path:
    """Create registry tables if missing."""
    db_path = registry_path(path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS table_mappings (
                oracle_schema TEXT NOT NULL,
                oracle_table TEXT NOT NULL,
                bigquery_project TEXT NOT NULL DEFAULT '',
                bigquery_dataset TEXT NOT NULL DEFAULT '',
                bigquery_table TEXT NOT NULL DEFAULT '',
                status TEXT NOT NULL DEFAULT 'pending',
                oracle_reachable INTEGER NOT NULL DEFAULT 0,
                oracle_checked_at TEXT NOT NULL DEFAULT '',
                bigquery_reachable INTEGER NOT NULL DEFAULT 0,
                bigquery_checked_at TEXT NOT NULL DEFAULT '',
                notes TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                PRIMARY KEY (oracle_schema, oracle_table)
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS column_mappings (
                oracle_schema TEXT NOT NULL,
                oracle_table TEXT NOT NULL,
                oracle_column TEXT NOT NULL,
                oracle_type TEXT NOT NULL DEFAULT '',
                bigquery_project TEXT NOT NULL DEFAULT '',
                bigquery_dataset TEXT NOT NULL DEFAULT '',
                bigquery_table TEXT NOT NULL DEFAULT '',
                bigquery_column TEXT NOT NULL DEFAULT '',
                bigquery_type TEXT NOT NULL DEFAULT '',
                oracle_present INTEGER NOT NULL DEFAULT 0,
                bigquery_present INTEGER NOT NULL DEFAULT 0,
                compatibility_status TEXT NOT NULL DEFAULT 'unknown',
                checked_at TEXT NOT NULL DEFAULT '',
                notes TEXT NOT NULL DEFAULT '',
                PRIMARY KEY (oracle_schema, oracle_table, oracle_column)
            )
            """
        )
    return db_path


def list_mappings(path: str | Path | None = None) -> list[TableMapping]:
    """Return all mappings sorted by source."""
    db_path = init_registry(path)
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            """
            SELECT oracle_schema, oracle_table, bigquery_project, bigquery_dataset, bigquery_table,
                   status, oracle_reachable, oracle_checked_at, bigquery_reachable,
                   bigquery_checked_at, notes
            FROM table_mappings
            ORDER BY oracle_schema, oracle_table
            """
        ).fetchall()
    return [_from_row(row) for row in rows]


def upsert_mapping(mapping: TableMapping, path: str | Path | None = None) -> None:
    """Insert or update one mapping."""
    db_path = init_registry(path)
    now = _now()
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO table_mappings (
                oracle_schema, oracle_table, bigquery_project, bigquery_dataset, bigquery_table,
                status, oracle_reachable, oracle_checked_at, bigquery_reachable,
                bigquery_checked_at, notes, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(oracle_schema, oracle_table) DO UPDATE SET
                bigquery_project=excluded.bigquery_project,
                bigquery_dataset=excluded.bigquery_dataset,
                bigquery_table=excluded.bigquery_table,
                status=excluded.status,
                oracle_reachable=excluded.oracle_reachable,
                oracle_checked_at=excluded.oracle_checked_at,
                bigquery_reachable=excluded.bigquery_reachable,
                bigquery_checked_at=excluded.bigquery_checked_at,
                notes=excluded.notes,
                updated_at=excluded.updated_at
            """,
            _to_params(mapping, now),
        )


def ensure_pending(oracle_schema: str, oracle_table: str, path: str | Path | None = None) -> TableMapping:
    """Ensure an unmapped source exists as pending and return its row."""
    existing = get_mapping(oracle_schema, oracle_table, path)
    if existing is not None:
        return existing
    mapping = TableMapping(oracle_schema=oracle_schema.lower(), oracle_table=oracle_table.lower())
    upsert_mapping(mapping, path)
    return mapping


def get_mapping(oracle_schema: str, oracle_table: str, path: str | Path | None = None) -> TableMapping | None:
    """Return one mapping by source table."""
    db_path = init_registry(path)
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            """
            SELECT oracle_schema, oracle_table, bigquery_project, bigquery_dataset, bigquery_table,
                   status, oracle_reachable, oracle_checked_at, bigquery_reachable,
                   bigquery_checked_at, notes
            FROM table_mappings
            WHERE oracle_schema = ? AND oracle_table = ?
            """,
            (oracle_schema.lower(), oracle_table.lower()),
        ).fetchone()
    return _from_row(row) if row else None


def seed_from_mapping_dict(
    mapping: dict[str, str],
    *,
    oracle_schema: str,
    bigquery_project: str,
    bigquery_dataset: str,
    path: str | Path | None = None,
) -> None:
    """Seed registry rows from the legacy simple source-to-target mapping."""
    for source, target in mapping.items():
        existing = get_mapping(oracle_schema, source, path)
        if existing is not None:
            continue
        upsert_mapping(
            TableMapping(
                oracle_schema=oracle_schema.lower(),
                oracle_table=source.lower(),
                bigquery_project=bigquery_project,
                bigquery_dataset=bigquery_dataset,
                bigquery_table=target.lower(),
                status="ready",
            ),
            path,
        )


def update_reachability(
    oracle_schema: str,
    oracle_table: str,
    *,
    oracle_reachable: bool | None = None,
    bigquery_reachable: bool | None = None,
    path: str | Path | None = None,
) -> TableMapping:
    """Update reachability flags and timestamps."""
    mapping = ensure_pending(oracle_schema, oracle_table, path)
    now = _now()
    if oracle_reachable is not None:
        mapping.oracle_reachable = oracle_reachable
        mapping.oracle_checked_at = now
    if bigquery_reachable is not None:
        mapping.bigquery_reachable = bigquery_reachable
        mapping.bigquery_checked_at = now
    if mapping.bigquery_table and mapping.oracle_reachable and mapping.bigquery_reachable:
        mapping.status = "ready"
    elif mapping.status == "ready":
        mapping.status = "blocked"
    upsert_mapping(mapping, path)
    return mapping


def mapping_dict(path: str | Path | None = None) -> dict[str, str]:
    """Return ready source-to-target table mappings for the translator."""
    return {row.oracle_table: row.bigquery_table for row in list_mappings(path) if row.ready}


def export_csv(path: str | Path | None = None) -> str:
    """Export registry rows as CSV text."""
    output = io.StringIO()
    writer = csv.DictWriter(output, fieldnames=CSV_COLUMNS)
    writer.writeheader()
    for row in list_mappings(path):
        data = asdict(row)
        data["oracle_reachable"] = str(row.oracle_reachable).lower()
        data["bigquery_reachable"] = str(row.bigquery_reachable).lower()
        writer.writerow({key: data.get(key, "") for key in CSV_COLUMNS})
    return output.getvalue()


def template_csv() -> str:
    """Return a user-fillable CSV template."""
    output = io.StringIO()
    writer = csv.DictWriter(output, fieldnames=CSV_COLUMNS)
    writer.writeheader()
    writer.writerow(
        {
            "oracle_schema": "DEMO",
            "oracle_table": "sales_orders",
            "bigquery_project": "mock-gcp-project",
            "bigquery_dataset": "mock_dataset",
            "bigquery_table": "raw_sales_orders",
            "status": "ready",
            "oracle_reachable": "false",
            "bigquery_reachable": "false",
            "notes": "example row; replace or delete",
        }
    )
    return output.getvalue()


def import_csv(csv_text: str, path: str | Path | None = None) -> int:
    """Import mappings from CSV text and return row count."""
    reader = csv.DictReader(io.StringIO(csv_text))
    if not reader.fieldnames:
        raise ValueError("CSV has no header")
    missing = [column for column in ("oracle_schema", "oracle_table") if column not in reader.fieldnames]
    if missing:
        raise ValueError(f"CSV missing required column(s): {', '.join(missing)}")
    count = 0
    for raw in reader:
        if not raw.get("oracle_schema") or not raw.get("oracle_table"):
            continue
        upsert_mapping(
            TableMapping(
                oracle_schema=str(raw.get("oracle_schema", "")).strip().lower(),
                oracle_table=str(raw.get("oracle_table", "")).strip().lower(),
                bigquery_project=str(raw.get("bigquery_project", "")).strip(),
                bigquery_dataset=str(raw.get("bigquery_dataset", "")).strip(),
                bigquery_table=str(raw.get("bigquery_table", "")).strip().lower(),
                status=str(raw.get("status", "pending") or "pending").strip().lower(),
                oracle_reachable=_to_bool(raw.get("oracle_reachable")),
                oracle_checked_at=str(raw.get("oracle_checked_at", "") or "").strip(),
                bigquery_reachable=_to_bool(raw.get("bigquery_reachable")),
                bigquery_checked_at=str(raw.get("bigquery_checked_at", "") or "").strip(),
                notes=str(raw.get("notes", "") or "").strip(),
            ),
            path,
        )
        count += 1
    return count


def _from_row(row: sqlite3.Row) -> TableMapping:
    return TableMapping(
        oracle_schema=str(row["oracle_schema"]),
        oracle_table=str(row["oracle_table"]),
        bigquery_project=str(row["bigquery_project"]),
        bigquery_dataset=str(row["bigquery_dataset"]),
        bigquery_table=str(row["bigquery_table"]),
        status=str(row["status"]),
        oracle_reachable=bool(row["oracle_reachable"]),
        oracle_checked_at=str(row["oracle_checked_at"]),
        bigquery_reachable=bool(row["bigquery_reachable"]),
        bigquery_checked_at=str(row["bigquery_checked_at"]),
        notes=str(row["notes"]),
    )


def _to_params(mapping: TableMapping, now: str) -> tuple[Any, ...]:
    return (
        mapping.oracle_schema.lower(),
        mapping.oracle_table.lower(),
        mapping.bigquery_project,
        mapping.bigquery_dataset,
        mapping.bigquery_table.lower(),
        mapping.status,
        int(mapping.oracle_reachable),
        mapping.oracle_checked_at,
        int(mapping.bigquery_reachable),
        mapping.bigquery_checked_at,
        mapping.notes,
        now,
        now,
    )


def _to_bool(value: object) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "y"}


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")
