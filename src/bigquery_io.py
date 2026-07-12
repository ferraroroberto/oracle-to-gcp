"""Generic BigQuery query execution, schema comparison, and load helpers.

Real (non-mock) GCP client wiring. ``google-cloud-bigquery`` is imported
lazily inside each function so the mock pipeline never requires it to be
installed — same convention as ``unit_test/schema_compatibility_audit.py``'s
Oracle/BigQuery metadata adapters. Consolidates query-execution and
schema/dtype-coercion logic previously duplicated across sibling fleet repos
(see issue #17).
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from typing import Any

import pandas as pd

log = logging.getLogger(__name__)

BIGQUERY_NUMERIC_TYPES = {"INTEGER", "INT64", "FLOAT", "FLOAT64", "NUMERIC", "BIGNUMERIC"}


def _bigquery_module():
    try:
        from google.cloud import bigquery
    except ImportError as exc:
        raise RuntimeError("Install google-cloud-bigquery to use the BigQuery helpers") from exc
    return bigquery


def get_client(project: str | None = None) -> Any:
    """Return a BigQuery client authenticated via ADC / ``GOOGLE_APPLICATION_CREDENTIALS``."""
    return _bigquery_module().Client(project=project)


def _scalar_query_parameter(bigquery: Any, name: str, value: Any) -> Any:
    if isinstance(value, bool):
        bq_type = "BOOL"
    elif isinstance(value, int):
        bq_type = "INT64"
    elif isinstance(value, float):
        bq_type = "FLOAT64"
    else:
        bq_type = "STRING"
        value = str(value)
    return bigquery.ScalarQueryParameter(name, bq_type, value)


def run_query(
    sql: str,
    project: str | None = None,
    params: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Run a parameterized query and return result rows as dicts."""
    bigquery = _bigquery_module()
    client = get_client(project)
    job_config = None
    if params:
        job_config = bigquery.QueryJobConfig(
            query_parameters=[_scalar_query_parameter(bigquery, name, value) for name, value in params.items()]
        )
    log.info("Running BigQuery query (%d chars, %d params)", len(sql), len(params or {}))
    rows = client.query(sql, job_config=job_config).result()
    return [dict(row.items()) for row in rows]


def execute_script(sql_script: str, project: str | None = None) -> None:
    """Execute a (possibly multi-statement) BigQuery script and block until done."""
    client = get_client(project)
    log.info("Executing BigQuery script (%d chars)", len(sql_script))
    client.query(sql_script).result()
    log.info("BigQuery script executed successfully")


def fetch_table_schema(table_ref: str, project: str | None = None) -> list[Any]:
    """Return the live schema (list of ``SchemaField``) for ``project.dataset.table``."""
    client = get_client(project)
    return list(client.get_table(table_ref).schema)


def load_dataframe(
    df: pd.DataFrame,
    table_ref: str,
    project: str | None = None,
    location: str | None = None,
    write_disposition: str = "WRITE_APPEND",
) -> int:
    """Load a DataFrame into a BigQuery table. Returns the row count loaded."""
    if df.empty:
        log.warning("No rows to load into %s", table_ref)
        return 0
    bigquery = _bigquery_module()
    client = get_client(project)
    job_config = bigquery.LoadJobConfig(write_disposition=write_disposition)
    job = client.load_table_from_dataframe(df, table_ref, job_config=job_config, location=location)
    job.result()
    log.info("Loaded %d rows into %s", len(df), table_ref)
    return len(df)


def map_pandas_dtype(series: pd.Series) -> str:
    """Rough mapping of a pandas ``Series``' dtype to a BigQuery type name."""
    dtype = str(series.dtype)
    if dtype.startswith(("datetime", "datetimetz")):
        return "DATETIME"
    if dtype == "bool":
        return "BOOL"
    if dtype.startswith("int") or dtype.startswith("float"):
        return "FLOAT64"
    return "STRING"


def compare_dataframe_schema(
    df: pd.DataFrame,
    bq_schema: Sequence[Any],
    lenient_datetime_columns: Sequence[str] = (),
) -> tuple[bool, pd.DataFrame]:
    """Compare a DataFrame's columns against a live BigQuery schema.

    Coerces compatible types (string/numeric/boolean) and drops columns
    absent from the target schema. ``lenient_datetime_columns`` names
    columns that must map to a DATETIME/TIMESTAMP BigQuery column but skip
    the general type-coercion branch (e.g. a partition/snapshot key column).

    Returns ``(all_ok, converted_df)`` — ``all_ok`` is False if any column
    hit an unresolvable type mismatch.
    """
    bq_cols = {sch.name: sch for sch in bq_schema}
    lenient = set(lenient_datetime_columns)

    info_messages: list[str] = []
    warning_messages: list[str] = []
    conversion_messages: list[str] = []
    all_ok = True
    df_converted = df.copy()
    columns_to_keep: list[str] = []

    for col in df.columns:
        if col not in bq_cols:
            info_messages.append(f"Ignoring column '{col}' (not present in BigQuery schema)")
            continue

        columns_to_keep.append(col)
        bq_type = bq_cols[col].field_type.upper()
        excel_type = map_pandas_dtype(df[col])

        if col in lenient:
            if bq_type not in {"DATETIME", "TIMESTAMP"}:
                warning_messages.append(f"Column {col} must be DATETIME/TIMESTAMP in BigQuery. Found {bq_type}.")
                all_ok = False
            else:
                info_messages.append(f"{col}: source DATETIME compatible with BigQuery {bq_type}")
            continue

        if bq_type == "STRING":
            df_converted[col] = df_converted[col].astype(str)
            conversion_messages.append(f"Converting {col}: source {excel_type} -> BigQuery STRING")
        elif bq_type in {"BOOL", "BOOLEAN"}:
            if excel_type == "BOOL":
                info_messages.append(f"{col}: source BOOL vs BigQuery {bq_type}")
            elif excel_type == "FLOAT64":
                unique_vals = df_converted[col].dropna().unique()
                if len(unique_vals) <= 2 and all(val in {0, 1, 0.0, 1.0, True, False} for val in unique_vals):
                    df_converted[col] = df_converted[col].fillna(False).astype(bool)
                    conversion_messages.append(
                        f"Converting {col}: source FLOAT64 (with nulls) -> BigQuery BOOLEAN (nulls set to False)"
                    )
                else:
                    warning_messages.append(f"{col} type mismatch (source FLOAT -> BigQuery {bq_type})")
                    all_ok = False
            else:
                warning_messages.append(f"{col} type mismatch (source {excel_type} -> BigQuery {bq_type})")
                all_ok = False
        elif excel_type == "BOOL" and bq_type not in {"BOOL", "BOOLEAN"}:
            warning_messages.append(f"{col} type mismatch (source BOOL -> BigQuery {bq_type})")
            all_ok = False
        elif excel_type == "FLOAT64" and bq_type == "INTEGER":
            df_converted[col] = df_converted[col].astype("Int64")
            conversion_messages.append(f"Converting {col}: source FLOAT64 -> BigQuery INTEGER")
        elif excel_type == "FLOAT64" and bq_type == "FLOAT":
            df_converted[col] = df_converted[col].astype("float64")
            conversion_messages.append(f"Converting {col}: source FLOAT64 -> BigQuery FLOAT")
        elif excel_type == "STRING" and bq_type in BIGQUERY_NUMERIC_TYPES:
            if bq_type in {"INTEGER", "INT64"}:
                df_converted[col] = pd.to_numeric(df_converted[col], errors="coerce").astype("Int64")
            else:
                df_converted[col] = pd.to_numeric(df_converted[col], errors="coerce")
            conversion_messages.append(
                f"Converting {col}: source STRING -> BigQuery {bq_type} (non-numeric values set to NULL)"
            )
        elif excel_type == "FLOAT64" and bq_type in BIGQUERY_NUMERIC_TYPES:
            info_messages.append(f"{col}: source FLOAT64 vs BigQuery {bq_type}")
        elif excel_type == "FLOAT64" and bq_type not in BIGQUERY_NUMERIC_TYPES.union({"STRING"}):
            warning_messages.append(f"{col} numeric mismatch (source FLOAT -> BigQuery {bq_type})")
            all_ok = False
        else:
            info_messages.append(f"{col}: source {excel_type} vs BigQuery {bq_type}")

    df_converted = df_converted[columns_to_keep]

    for col in bq_cols:
        if col not in df.columns:
            warning_messages.append(f"BigQuery column {col} missing from source. Will be NULL.")

    for msg in info_messages:
        log.info(msg)
    for msg in conversion_messages:
        log.info(msg)
    for msg in warning_messages:
        log.warning(msg)

    return all_ok, df_converted
