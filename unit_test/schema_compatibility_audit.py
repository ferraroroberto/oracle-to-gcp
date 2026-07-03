"""Standalone Oracle-to-BigQuery column compatibility audit.

The audit consumes table correspondences, fetches authoritative column metadata
from each side, compares column presence and type families, and writes
machine-readable reports for the SQL translation step.
"""

from __future__ import annotations

import argparse
import csv
import json
import logging
import os
import re
import sqlite3
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Protocol

log = logging.getLogger("schema_compatibility_audit")

DEFAULT_CONFIG_PATH = Path(__file__).with_name("schema_audit_config.json")
SAFE_IDENTIFIER = re.compile(r"^[A-Za-z0-9_-]+$")


@dataclass(slots=True)
class TablePair:
    """One source Oracle table mapped to one target BigQuery table."""

    oracle_schema: str
    oracle_table: str
    bigquery_project: str
    bigquery_dataset: str
    bigquery_table: str


@dataclass(slots=True)
class ColumnMetadata:
    """Database-native column metadata used as the comparison source of truth."""

    name: str
    raw_type: str
    type_family: str
    nullable: bool | None = None
    ordinal: int | None = None
    length: int | None = None
    precision: int | None = None
    scale: int | None = None


@dataclass(slots=True)
class ColumnComparison:
    """One source-target column comparison row."""

    oracle_schema: str
    oracle_table: str
    bigquery_project: str
    bigquery_dataset: str
    bigquery_table: str
    oracle_column: str
    bigquery_column: str
    oracle_type: str
    bigquery_type: str
    oracle_type_family: str
    bigquery_type_family: str
    oracle_present: bool
    bigquery_present: bool
    compatibility_status: str
    severity: str
    oracle_nullable: bool | None
    bigquery_nullable: bool | None
    oracle_length: int | None
    bigquery_length: int | None
    oracle_precision: int | None
    bigquery_precision: int | None
    oracle_scale: int | None
    bigquery_scale: int | None
    oracle_sample_non_null: int | None
    bigquery_sample_non_null: int | None
    oracle_sample_values: str
    bigquery_sample_values: str
    notes: str


@dataclass(slots=True)
class SampleSummary:
    """Small data sample summary used only as supporting evidence."""

    non_null_count: int
    example_values: list[str]


@dataclass(slots=True)
class TableSummary:
    """Aggregated audit status for one table pair."""

    oracle_schema: str
    oracle_table: str
    bigquery_project: str
    bigquery_dataset: str
    bigquery_table: str
    total_columns: int
    compatible_columns: int
    warning_columns: int
    error_columns: int
    status: str


class MetadataAdapter(Protocol):
    """Database-specific source of authoritative column metadata."""

    def columns_for(self, pair: TablePair, side: str) -> list[ColumnMetadata]:
        """Return column metadata for one table pair side."""

    def sample_for(
        self,
        pair: TablePair,
        side: str,
        columns: list[ColumnMetadata],
        limit: int,
    ) -> dict[str, SampleSummary]:
        """Return small sample summaries keyed by normalized column name."""


class SqliteMetadataAdapter:
    """Metadata adapter for local mock databases and fast experiments."""

    def __init__(self, db_path: str) -> None:
        self.db_path = db_path

    def columns_for(self, pair: TablePair, side: str) -> list[ColumnMetadata]:
        """Return SQLite ``PRAGMA table_info`` rows."""
        table = pair.oracle_table if side == "oracle" else pair.bigquery_table
        log.info("Fetching SQLite metadata for %s table %s", side, table)
        with sqlite3.connect(self.db_path) as conn:
            rows = conn.execute(f"PRAGMA table_info({_quote_sqlite_identifier(table)})").fetchall()
        return [
            ColumnMetadata(
                name=str(row[1]),
                raw_type=str(row[2] or ""),
                type_family=type_family(str(row[2] or "")),
                nullable=not bool(row[3]),
                ordinal=int(row[0]) + 1,
            )
            for row in rows
        ]

    def sample_for(
        self,
        pair: TablePair,
        side: str,
        columns: list[ColumnMetadata],
        limit: int,
    ) -> dict[str, SampleSummary]:
        """Return SQLite sample summaries."""
        table = pair.oracle_table if side == "oracle" else pair.bigquery_table
        if not columns or limit <= 0:
            return {}
        column_sql = ", ".join(_quote_sqlite_identifier(column.name) for column in columns)
        query = f"SELECT {column_sql} FROM {_quote_sqlite_identifier(table)} LIMIT ?"
        with sqlite3.connect(self.db_path) as conn:
            rows = conn.execute(query, (limit,)).fetchall()
        return _summarize_samples(columns, rows)


class OracleMetadataAdapter:
    """Oracle adapter backed by data dictionary metadata."""

    def __init__(self, config: dict[str, Any]) -> None:
        self.config = config

    def columns_for(self, pair: TablePair, side: str) -> list[ColumnMetadata]:
        """Return Oracle ``ALL_TAB_COLUMNS`` metadata."""
        if side != "oracle":
            raise ValueError("OracleMetadataAdapter can only serve the oracle side")
        try:
            import oracledb
        except ImportError as exc:
            raise RuntimeError("Install python-oracledb to use the Oracle metadata adapter") from exc

        user = _env_value(self.config, "username_env_var")
        password = _env_value(self.config, "password_env_var")
        dsn = _env_value(self.config, "dsn_env_var")
        query = """
            SELECT column_name, data_type, data_length, data_precision, data_scale,
                   nullable, column_id
            FROM all_tab_columns
            WHERE owner = UPPER(:owner)
              AND table_name = UPPER(:table_name)
            ORDER BY column_id
        """
        log.info("Fetching Oracle metadata from ALL_TAB_COLUMNS for %s.%s", pair.oracle_schema, pair.oracle_table)
        with oracledb.connect(user=user, password=password, dsn=dsn) as conn:
            with conn.cursor() as cursor:
                cursor.execute(query, owner=pair.oracle_schema, table_name=pair.oracle_table)
                rows = cursor.fetchall()
        return [
            ColumnMetadata(
                name=str(row[0]),
                raw_type=str(row[1] or ""),
                type_family=type_family(str(row[1] or "")),
                length=_optional_int(row[2]),
                precision=_optional_int(row[3]),
                scale=_optional_int(row[4]),
                nullable=str(row[5]).upper() == "Y",
                ordinal=_optional_int(row[6]),
            )
            for row in rows
        ]

    def sample_for(
        self,
        pair: TablePair,
        side: str,
        columns: list[ColumnMetadata],
        limit: int,
    ) -> dict[str, SampleSummary]:
        """Return Oracle sample summaries."""
        if side != "oracle":
            raise ValueError("OracleMetadataAdapter can only serve the oracle side")
        if not columns or limit <= 0:
            return {}
        try:
            import oracledb
        except ImportError as exc:
            raise RuntimeError("Install python-oracledb to use the Oracle metadata adapter") from exc

        _assert_safe_sql_identifier(pair.oracle_schema, "oracle_schema")
        _assert_safe_sql_identifier(pair.oracle_table, "oracle_table")
        for column in columns:
            _assert_safe_sql_identifier(column.name, "oracle_column")
        column_sql = ", ".join(column.name for column in columns)
        query = f"SELECT {column_sql} FROM {pair.oracle_schema}.{pair.oracle_table} FETCH FIRST {int(limit)} ROWS ONLY"
        with oracledb.connect(
            user=_env_value(self.config, "username_env_var"),
            password=_env_value(self.config, "password_env_var"),
            dsn=_env_value(self.config, "dsn_env_var"),
        ) as conn:
            with conn.cursor() as cursor:
                cursor.execute(query)
                rows = cursor.fetchall()
        return _summarize_samples(columns, rows)


class BigQueryMetadataAdapter:
    """BigQuery adapter backed by ``INFORMATION_SCHEMA.COLUMNS``."""

    def __init__(self, config: dict[str, Any]) -> None:
        self.config = config

    def columns_for(self, pair: TablePair, side: str) -> list[ColumnMetadata]:
        """Return BigQuery column metadata for the target table."""
        if side != "bigquery":
            raise ValueError("BigQueryMetadataAdapter can only serve the bigquery side")
        try:
            from google.cloud import bigquery
        except ImportError as exc:
            raise RuntimeError("Install google-cloud-bigquery to use the BigQuery metadata adapter") from exc

        project = pair.bigquery_project or self.config.get("project_id", "")
        dataset = pair.bigquery_dataset
        _assert_safe_bigquery_identifier(project, "bigquery_project")
        _assert_safe_bigquery_identifier(dataset, "bigquery_dataset")
        query = f"""
            SELECT column_name, data_type, is_nullable, ordinal_position
            FROM `{project}.{dataset}.INFORMATION_SCHEMA.COLUMNS`
            WHERE table_name = @table_name
            ORDER BY ordinal_position
        """
        log.info(
            "Fetching BigQuery metadata from %s.%s.INFORMATION_SCHEMA.COLUMNS for %s",
            project,
            dataset,
            pair.bigquery_table,
        )
        client = bigquery.Client(project=project)
        job_config = bigquery.QueryJobConfig(
            query_parameters=[
                bigquery.ScalarQueryParameter("table_name", "STRING", pair.bigquery_table),
            ]
        )
        rows = list(client.query(query, job_config=job_config).result())
        return [
            ColumnMetadata(
                name=str(row.column_name),
                raw_type=str(row.data_type or ""),
                type_family=type_family(str(row.data_type or "")),
                nullable=str(row.is_nullable).upper() == "YES",
                ordinal=_optional_int(row.ordinal_position),
                **_bigquery_numeric_shape(str(row.data_type or "")),
            )
            for row in rows
        ]

    def sample_for(
        self,
        pair: TablePair,
        side: str,
        columns: list[ColumnMetadata],
        limit: int,
    ) -> dict[str, SampleSummary]:
        """Return BigQuery sample summaries."""
        if side != "bigquery":
            raise ValueError("BigQueryMetadataAdapter can only serve the bigquery side")
        if not columns or limit <= 0:
            return {}
        try:
            from google.cloud import bigquery
        except ImportError as exc:
            raise RuntimeError("Install google-cloud-bigquery to use the BigQuery metadata adapter") from exc

        project = pair.bigquery_project or self.config.get("project_id", "")
        dataset = pair.bigquery_dataset
        table = pair.bigquery_table
        _assert_safe_bigquery_identifier(project, "bigquery_project")
        _assert_safe_bigquery_identifier(dataset, "bigquery_dataset")
        _assert_safe_bigquery_identifier(table, "bigquery_table")
        column_sql = ", ".join(_quote_bigquery_identifier(column.name) for column in columns)
        query = f"SELECT {column_sql} FROM `{project}.{dataset}.{table}` LIMIT @row_limit"
        client = bigquery.Client(project=project)
        job_config = bigquery.QueryJobConfig(
            query_parameters=[
                bigquery.ScalarQueryParameter("row_limit", "INT64", int(limit)),
            ]
        )
        rows = [tuple(row.values()) for row in client.query(query, job_config=job_config).result()]
        return _summarize_samples(columns, rows)


def run_audit(config_path: str | Path = DEFAULT_CONFIG_PATH) -> tuple[list[ColumnComparison], list[TableSummary]]:
    """Run the configured audit and write reports."""
    config = _load_json(config_path)
    _configure_logging(config.get("logging", {}))
    pairs = load_table_pairs(config.get("input", {}))
    oracle_adapter = build_adapter(config.get("oracle", {}), "oracle")
    bigquery_adapter = build_adapter(config.get("bigquery", {}), "bigquery")
    log.info("Loaded %d table correspondence row(s)", len(pairs))

    comparisons: list[ColumnComparison] = []
    summaries: list[TableSummary] = []
    for pair in pairs:
        log.info(
            "Auditing %s.%s -> %s.%s.%s",
            pair.oracle_schema,
            pair.oracle_table,
            pair.bigquery_project,
            pair.bigquery_dataset,
            pair.bigquery_table,
        )
        try:
            oracle_columns = oracle_adapter.columns_for(pair, "oracle")
            bigquery_columns = bigquery_adapter.columns_for(pair, "bigquery")
            oracle_samples, bigquery_samples = _sample_evidence(
                pair,
                oracle_adapter,
                bigquery_adapter,
                oracle_columns,
                bigquery_columns,
                config.get("sampling", {}),
            )
            table_rows = compare_columns(
                pair,
                oracle_columns,
                bigquery_columns,
                config.get("comparison", {}),
                oracle_samples=oracle_samples,
                bigquery_samples=bigquery_samples,
            )
        except Exception as exc:
            log.exception(
                "Metadata audit failed for %s.%s -> %s.%s.%s",
                pair.oracle_schema,
                pair.oracle_table,
                pair.bigquery_project,
                pair.bigquery_dataset,
                pair.bigquery_table,
            )
            table_rows = [_metadata_error_row(pair, exc)]
        comparisons.extend(table_rows)
        summaries.append(summarize_table(pair, table_rows))
    write_reports(comparisons, summaries, config.get("output", {}))
    return comparisons, summaries


def load_table_pairs(config: dict[str, Any]) -> list[TablePair]:
    """Load table correspondences from CSV or JSON."""
    input_path = Path(str(config.get("path", "")))
    if not input_path.exists():
        raise FileNotFoundError(f"Correspondence input not found: {input_path}")
    columns = config.get("columns", {})
    input_format = str(config.get("format", input_path.suffix.lstrip("."))).lower()
    if input_format == "csv":
        with input_path.open(newline="", encoding="utf-8-sig") as handle:
            rows = list(csv.DictReader(handle))
    elif input_format == "json":
        raw = json.loads(input_path.read_text(encoding="utf-8"))
        rows = raw.get("tables", raw) if isinstance(raw, dict) else raw
        if not isinstance(rows, list):
            raise ValueError("JSON correspondence input must be a list or an object with a 'tables' list")
    elif input_format in {"xlsx", "xls", "excel"}:
        try:
            import pandas as pd
        except ImportError as exc:
            raise RuntimeError("Install pandas and openpyxl to read Excel correspondence inputs") from exc
        rows = pd.read_excel(input_path).fillna("").to_dict(orient="records")
    else:
        raise ValueError(f"Unsupported input format: {input_format}")
    return [_table_pair_from_row(row, columns) for row in rows]


def build_adapter(config: dict[str, Any], side: str) -> MetadataAdapter:
    """Build the configured metadata adapter."""
    mode = str(config.get("mode", "sqlite")).lower()
    if mode == "sqlite":
        db_path = str(config.get("db_path", ""))
        if not db_path:
            raise ValueError(f"{side} sqlite adapter requires db_path")
        return SqliteMetadataAdapter(db_path)
    if mode == "oracle":
        return OracleMetadataAdapter(config)
    if mode == "bigquery":
        return BigQueryMetadataAdapter(config)
    raise ValueError(f"Unsupported {side} metadata adapter mode: {mode}")


def compare_columns(
    pair: TablePair,
    oracle_columns: list[ColumnMetadata],
    bigquery_columns: list[ColumnMetadata],
    config: dict[str, Any] | None = None,
    *,
    oracle_samples: dict[str, SampleSummary] | None = None,
    bigquery_samples: dict[str, SampleSummary] | None = None,
) -> list[ColumnComparison]:
    """Compare source and target columns for one table pair."""
    case_sensitive = bool((config or {}).get("case_sensitive_columns", False))
    oracle_by_name = {_column_key(column.name, case_sensitive): column for column in oracle_columns}
    bq_by_name = {_column_key(column.name, case_sensitive): column for column in bigquery_columns}
    ordered_keys = sorted(set(oracle_by_name) | set(bq_by_name))
    return [
        _compare_one(
            pair,
            oracle_by_name.get(key),
            bq_by_name.get(key),
            oracle_samples=(oracle_samples or {}).get(key),
            bigquery_samples=(bigquery_samples or {}).get(key),
        )
        for key in ordered_keys
    ]


def summarize_table(pair: TablePair, rows: list[ColumnComparison]) -> TableSummary:
    """Build a table-level summary from column comparison rows."""
    error_count = sum(1 for row in rows if row.severity == "error")
    warning_count = sum(1 for row in rows if row.severity == "warning")
    compatible_count = sum(1 for row in rows if row.severity == "ok")
    status = "blocked" if error_count else "warning" if warning_count else "compatible"
    return TableSummary(
        oracle_schema=pair.oracle_schema,
        oracle_table=pair.oracle_table,
        bigquery_project=pair.bigquery_project,
        bigquery_dataset=pair.bigquery_dataset,
        bigquery_table=pair.bigquery_table,
        total_columns=len(rows),
        compatible_columns=compatible_count,
        warning_columns=warning_count,
        error_columns=error_count,
        status=status,
    )


def write_reports(
    comparisons: list[ColumnComparison],
    summaries: list[TableSummary],
    output_config: dict[str, Any],
) -> None:
    """Write detailed and summary reports as CSV and JSON."""
    detail_csv = Path(str(output_config.get("detail_csv", "data/output/schema_audit/column_report.csv")))
    summary_csv = Path(str(output_config.get("summary_csv", "data/output/schema_audit/table_summary.csv")))
    detail_json = Path(str(output_config.get("detail_json", "data/output/schema_audit/column_report.json")))
    summary_json = Path(str(output_config.get("summary_json", "data/output/schema_audit/table_summary.json")))
    _write_csv(detail_csv, [asdict(row) for row in comparisons])
    _write_csv(summary_csv, [asdict(row) for row in summaries])
    _write_json(detail_json, [asdict(row) for row in comparisons])
    _write_json(summary_json, [asdict(row) for row in summaries])
    log.info("Wrote detail report: %s", detail_csv)
    log.info("Wrote summary report: %s", summary_csv)


def type_family(raw_type: str) -> str:
    """Normalize database-specific type text to a migration-relevant family."""
    normalized = raw_type.upper().strip()
    if any(token in normalized for token in ("CHAR", "CLOB", "STRING", "TEXT", "VARCHAR")):
        return "text"
    if any(token in normalized for token in ("DATE", "TIME", "TIMESTAMP", "DATETIME")):
        return "temporal"
    if any(token in normalized for token in ("NUMBER", "NUMERIC", "DECIMAL", "INT", "FLOAT", "DOUBLE", "REAL", "BIGNUMERIC")):
        return "numeric"
    if any(token in normalized for token in ("BOOL", "BOOLEAN")):
        return "boolean"
    if any(token in normalized for token in ("BLOB", "BYTES", "RAW", "BINARY")):
        return "bytes"
    if any(token in normalized for token in ("STRUCT", "RECORD", "ARRAY")):
        return "complex"
    if "JSON" in normalized:
        return "json"
    return normalized.lower() or "unknown"


def _compare_one(
    pair: TablePair,
    oracle: ColumnMetadata | None,
    bigquery: ColumnMetadata | None,
    *,
    oracle_samples: SampleSummary | None = None,
    bigquery_samples: SampleSummary | None = None,
) -> ColumnComparison:
    oracle_present = oracle is not None
    bigquery_present = bigquery is not None
    notes: list[str] = []
    if not oracle_present:
        status = "extra_target"
        severity = "warning"
        notes.append("Column exists only in BigQuery target")
    elif not bigquery_present:
        status = "missing_target"
        severity = "error"
        notes.append("Column exists in Oracle source but is missing from BigQuery target")
    elif oracle.type_family != bigquery.type_family:
        status = "type_mismatch"
        severity = "error"
        notes.append(f"Type family changed from {oracle.type_family} to {bigquery.type_family}")
    else:
        status = "compatible"
        severity = "ok"

    if oracle_present and bigquery_present and oracle.type_family == bigquery.type_family:
        warnings = _compatibility_warnings(oracle, bigquery)
        if warnings:
            status = "compatible_with_warnings"
            severity = "warning"
            notes.extend(warnings)

    return ColumnComparison(
        oracle_schema=pair.oracle_schema,
        oracle_table=pair.oracle_table,
        bigquery_project=pair.bigquery_project,
        bigquery_dataset=pair.bigquery_dataset,
        bigquery_table=pair.bigquery_table,
        oracle_column=oracle.name if oracle else "",
        bigquery_column=bigquery.name if bigquery else "",
        oracle_type=oracle.raw_type if oracle else "",
        bigquery_type=bigquery.raw_type if bigquery else "",
        oracle_type_family=oracle.type_family if oracle else "",
        bigquery_type_family=bigquery.type_family if bigquery else "",
        oracle_present=oracle_present,
        bigquery_present=bigquery_present,
        compatibility_status=status,
        severity=severity,
        oracle_nullable=oracle.nullable if oracle else None,
        bigquery_nullable=bigquery.nullable if bigquery else None,
        oracle_length=oracle.length if oracle else None,
        bigquery_length=bigquery.length if bigquery else None,
        oracle_precision=oracle.precision if oracle else None,
        bigquery_precision=bigquery.precision if bigquery else None,
        oracle_scale=oracle.scale if oracle else None,
        bigquery_scale=bigquery.scale if bigquery else None,
        oracle_sample_non_null=oracle_samples.non_null_count if oracle_samples else None,
        bigquery_sample_non_null=bigquery_samples.non_null_count if bigquery_samples else None,
        oracle_sample_values=json.dumps(oracle_samples.example_values) if oracle_samples else "",
        bigquery_sample_values=json.dumps(bigquery_samples.example_values) if bigquery_samples else "",
        notes="; ".join(notes),
    )


def _metadata_error_row(pair: TablePair, exc: Exception) -> ColumnComparison:
    return ColumnComparison(
        oracle_schema=pair.oracle_schema,
        oracle_table=pair.oracle_table,
        bigquery_project=pair.bigquery_project,
        bigquery_dataset=pair.bigquery_dataset,
        bigquery_table=pair.bigquery_table,
        oracle_column="",
        bigquery_column="",
        oracle_type="",
        bigquery_type="",
        oracle_type_family="",
        bigquery_type_family="",
        oracle_present=False,
        bigquery_present=False,
        compatibility_status="metadata_error",
        severity="error",
        oracle_nullable=None,
        bigquery_nullable=None,
        oracle_length=None,
        bigquery_length=None,
        oracle_precision=None,
        bigquery_precision=None,
        oracle_scale=None,
        bigquery_scale=None,
        oracle_sample_non_null=None,
        bigquery_sample_non_null=None,
        oracle_sample_values="",
        bigquery_sample_values="",
        notes=f"{type(exc).__name__}: {exc}",
    )


def _sample_evidence(
    pair: TablePair,
    oracle_adapter: MetadataAdapter,
    bigquery_adapter: MetadataAdapter,
    oracle_columns: list[ColumnMetadata],
    bigquery_columns: list[ColumnMetadata],
    config: dict[str, Any],
) -> tuple[dict[str, SampleSummary], dict[str, SampleSummary]]:
    if not bool(config.get("enabled", False)):
        return {}, {}
    limit = int(config.get("max_rows", 1000))
    log.info("Collecting sample evidence with max_rows=%d", limit)
    return (
        oracle_adapter.sample_for(pair, "oracle", oracle_columns, limit),
        bigquery_adapter.sample_for(pair, "bigquery", bigquery_columns, limit),
    )


def _summarize_samples(columns: list[ColumnMetadata], rows: list[Any]) -> dict[str, SampleSummary]:
    examples: dict[str, list[str]] = {_column_key(column.name, False): [] for column in columns}
    non_null_counts: dict[str, int] = {_column_key(column.name, False): 0 for column in columns}
    for row in rows:
        values = tuple(row)
        for index, column in enumerate(columns):
            key = _column_key(column.name, False)
            value = values[index] if index < len(values) else None
            if value is None:
                continue
            non_null_counts[key] += 1
            rendered = str(value)
            if rendered not in examples[key] and len(examples[key]) < 3:
                examples[key].append(rendered)
    return {
        key: SampleSummary(non_null_count=non_null_counts[key], example_values=examples[key])
        for key in non_null_counts
    }


def _compatibility_warnings(oracle: ColumnMetadata, bigquery: ColumnMetadata) -> list[str]:
    warnings: list[str] = []
    if oracle.nullable is True and bigquery.nullable is False:
        warnings.append("BigQuery column is more restrictive: source nullable, target required")
    if oracle.precision and bigquery.precision and bigquery.precision < oracle.precision:
        warnings.append(f"BigQuery precision {bigquery.precision} is lower than Oracle precision {oracle.precision}")
    if oracle.scale and bigquery.scale is not None and bigquery.scale < oracle.scale:
        warnings.append(f"BigQuery scale {bigquery.scale} is lower than Oracle scale {oracle.scale}")
    if oracle.length and bigquery.length and bigquery.length < oracle.length:
        warnings.append(f"BigQuery length {bigquery.length} is lower than Oracle length {oracle.length}")
    return warnings


def _table_pair_from_row(row: dict[str, Any], columns: dict[str, str]) -> TablePair:
    def value(key: str) -> str:
        source_key = columns.get(key, key)
        raw = row.get(source_key, "")
        return str(raw or "").strip()

    pair = TablePair(
        oracle_schema=value("oracle_schema"),
        oracle_table=value("oracle_table"),
        bigquery_project=value("bigquery_project"),
        bigquery_dataset=value("bigquery_dataset"),
        bigquery_table=value("bigquery_table"),
    )
    missing = [field for field, field_value in asdict(pair).items() if not field_value]
    if missing:
        raise ValueError(f"Table correspondence row missing required field(s): {', '.join(missing)}")
    return pair


def _column_key(name: str, case_sensitive: bool) -> str:
    return name if case_sensitive else name.lower()


def _load_json(path: str | Path) -> dict[str, Any]:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = list(rows[0].keys()) if rows else []
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        if fieldnames:
            writer.writeheader()
            writer.writerows(rows)


def _write_json(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(rows, indent=2), encoding="utf-8")


def _configure_logging(config: dict[str, Any]) -> None:
    level_name = str(config.get("level", "INFO")).upper()
    logging.basicConfig(level=getattr(logging, level_name, logging.INFO), format="%(asctime)s %(levelname)s %(message)s")


def _env_value(config: dict[str, Any], key: str) -> str:
    env_var = str(config.get(key, ""))
    if not env_var:
        raise ValueError(f"Missing config key: {key}")
    value = os.getenv(env_var)
    if not value:
        raise ValueError(f"Environment variable is required but empty: {env_var}")
    return value


def _quote_sqlite_identifier(identifier: str) -> str:
    return '"' + identifier.replace('"', '""') + '"'


def _assert_safe_bigquery_identifier(value: str, name: str) -> None:
    if not SAFE_IDENTIFIER.match(value):
        raise ValueError(f"Unsafe {name}: {value!r}")


def _assert_safe_sql_identifier(value: str, name: str) -> None:
    if not re.match(r"^[A-Za-z][A-Za-z0-9_$#]*$", value):
        raise ValueError(f"Unsafe {name}: {value!r}")


def _quote_bigquery_identifier(identifier: str) -> str:
    return "`" + identifier.replace("`", "``") + "`"


def _optional_int(value: object) -> int | None:
    if value is None or value == "":
        return None
    return int(value)


def _bigquery_numeric_shape(raw_type: str) -> dict[str, int | None]:
    normalized = raw_type.upper()
    if normalized == "NUMERIC":
        return {"precision": 38, "scale": 9}
    if normalized == "BIGNUMERIC":
        return {"precision": 76, "scale": 38}
    if normalized == "INT64":
        return {"precision": 19, "scale": 0}
    return {"precision": None, "scale": None}


def main(argv: list[str] | None = None) -> int:
    """CLI entry point."""
    parser = argparse.ArgumentParser(description="Audit Oracle-to-BigQuery schema compatibility.")
    parser.add_argument("--config", default=str(DEFAULT_CONFIG_PATH), help="Path to schema audit JSON config.")
    args = parser.parse_args(argv)
    run_audit(args.config)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
