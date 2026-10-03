"""Platform adapters — the one seam between the semantic layer and a warehouse.

Every tool that touches a database goes through :class:`Platform`. The mock
adapter runs on DuckDB and transpiles the platform's dialect with sqlglot; the
real adapters (BigQuery, Oracle) lazy-import their client libraries so the mock
never needs them installed. **Porting = switching ``config.json`` adapters**,
not rewriting callers.

The real adapters' metadata queries follow the idioms of this repo's
``unit_test/schema_compatibility_audit.py`` (``ALL_TAB_COLUMNS`` for Oracle,
``INFORMATION_SCHEMA.COLUMNS`` for BigQuery), copied rather than imported so
``semantic_layer/`` stays self-contained.

Only aggregate queries are run through :meth:`Platform.query` by the tools in
this package (probes, checks, answers) — row-level data never leaves a
platform into reports, issues or prompts.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from typing import Any, Protocol

import sqlglot

from semantic_layer.sl.common import Layer, LayerError, to_jsonable

_SAFE_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_$#-]*$")


class PlatformUnavailable(Exception):
    """The platform could not be reached; callers must report ``unknown``, never pass."""


@dataclass
class DryRunResult:
    ok: bool
    detail: str
    bytes_estimate: int | None = None


@dataclass
class Column:
    name: str
    type: str
    nullable: bool = True


class Platform(Protocol):
    name: str
    dialect: str

    def tables(self) -> list[str]: ...

    def columns(self, table: str) -> list[Column]: ...

    def dry_run(self, sql: str) -> DryRunResult: ...

    def query(self, sql: str, max_rows: int = 500) -> list[dict[str, Any]]: ...


def split_table(table: str) -> tuple[str, str]:
    """``'analytics.customers'`` → ``('analytics', 'customers')``; the last two parts win."""
    parts = table.replace("`", "").split(".")
    if len(parts) < 2:
        raise LayerError(f"Table '{table}' must be schema-qualified (schema.table)")
    schema, name = parts[-2], parts[-1]
    for part in (schema, name):
        if not _SAFE_IDENTIFIER.match(part):
            raise LayerError(f"Unsafe identifier in table reference '{table}'")
    return schema, name


@dataclass
class MockDuckDBPlatform:
    """DuckDB file standing in for a warehouse; SQL arrives in ``dialect``."""

    name: str
    dialect: str
    path: str
    _schemas_hidden: tuple[str, ...] = field(default=("information_schema", "pg_catalog", "main"))

    def _connect(self) -> Any:
        import duckdb

        if not os.path.exists(self.path):
            raise PlatformUnavailable(f"mock warehouse not found at {self.path} (run `sl mock seed`)")
        return duckdb.connect(self.path, read_only=True)

    def transpile(self, sql: str) -> str:
        try:
            return ";\n".join(sqlglot.transpile(sql, read=self.dialect, write="duckdb"))
        except sqlglot.errors.ParseError as exc:
            raise LayerError(f"Could not parse {self.dialect} SQL: {exc}") from exc

    def tables(self) -> list[str]:
        with self._connect() as con:
            rows = con.execute(
                "SELECT table_schema, table_name FROM information_schema.tables ORDER BY 1, 2"
            ).fetchall()
        return [f"{schema}.{table}" for schema, table in rows if schema.lower() not in self._schemas_hidden]

    def columns(self, table: str) -> list[Column]:
        schema, name = split_table(table)
        with self._connect() as con:
            rows = con.execute(
                "SELECT column_name, data_type, is_nullable FROM information_schema.columns "
                "WHERE lower(table_schema) = lower(?) AND lower(table_name) = lower(?) ORDER BY ordinal_position",
                [schema, name],
            ).fetchall()
        return [Column(name=str(r[0]), type=str(r[1]), nullable=str(r[2]).upper() == "YES") for r in rows]

    def dry_run(self, sql: str) -> DryRunResult:
        try:
            translated = self.transpile(sql)
            with self._connect() as con:
                con.execute(f"EXPLAIN {translated}").fetchall()
        except PlatformUnavailable:
            raise
        except Exception as exc:  # noqa: BLE001 — a dry run reports any engine error as a failed check
            return DryRunResult(ok=False, detail=f"{type(exc).__name__}: {exc}")
        return DryRunResult(ok=True, detail="EXPLAIN succeeded on the mock warehouse (DuckDB)")

    def query(self, sql: str, max_rows: int = 500) -> list[dict[str, Any]]:
        translated = self.transpile(sql)
        with self._connect() as con:
            cursor = con.execute(translated)
            names = [d[0] for d in cursor.description]
            rows = cursor.fetchmany(max_rows)
        return [{n: to_jsonable(v) for n, v in zip(names, row, strict=True)} for row in rows]


@dataclass
class BigQueryPlatform:
    """Real BigQuery: dry runs estimate bytes without scanning; metadata from the table API."""

    name: str
    dialect: str
    project: str
    datasets: list[str]

    def _client(self) -> Any:
        try:
            from google.cloud import bigquery
        except ImportError as exc:
            raise PlatformUnavailable("Install google-cloud-bigquery to use the BigQuery adapter") from exc
        try:
            return bigquery.Client(project=self.project or None)
        except Exception as exc:  # noqa: BLE001 — auth/config failures mean 'unreachable'
            raise PlatformUnavailable(f"BigQuery client unavailable: {exc}") from exc

    def tables(self) -> list[str]:
        client = self._client()
        found: list[str] = []
        for dataset in self.datasets:
            found.extend(f"{dataset}.{t.table_id}" for t in client.list_tables(dataset))
        return sorted(found)

    def columns(self, table: str) -> list[Column]:
        schema, name = split_table(table)
        client = self._client()
        bq_table = client.get_table(f"{self.project}.{schema}.{name}" if self.project else f"{schema}.{name}")
        return [Column(name=f.name, type=f.field_type, nullable=f.mode != "REQUIRED") for f in bq_table.schema]

    def dry_run(self, sql: str) -> DryRunResult:
        from google.cloud import bigquery

        client = self._client()
        try:
            job = client.query(sql, job_config=bigquery.QueryJobConfig(dry_run=True, use_query_cache=False))
        except Exception as exc:  # noqa: BLE001 — surfaced as a failed validation
            return DryRunResult(ok=False, detail=f"{type(exc).__name__}: {exc}")
        return DryRunResult(ok=True, detail="BigQuery dry run succeeded", bytes_estimate=job.total_bytes_processed)

    def query(self, sql: str, max_rows: int = 500) -> list[dict[str, Any]]:
        client = self._client()
        rows = client.query(sql).result(max_results=max_rows)
        return [{k: to_jsonable(v) for k, v in row.items()} for row in rows]


@dataclass
class OraclePlatform:
    """Real Oracle: ``EXPLAIN PLAN`` as the dry run, ``ALL_TAB_COLUMNS`` for metadata."""

    name: str
    dialect: str
    username_env: str
    password_env: str
    dsn_env: str
    schemas: list[str]

    def _connect(self) -> Any:
        try:
            import oracledb
        except ImportError as exc:
            raise PlatformUnavailable("Install python-oracledb to use the Oracle adapter") from exc
        values = {key: os.getenv(env, "") for key, env in
                  (("user", self.username_env), ("password", self.password_env), ("dsn", self.dsn_env))}
        missing = [key for key, value in values.items() if not value]
        if missing:
            raise PlatformUnavailable(f"Oracle connection env vars not set: {', '.join(missing)}")
        try:
            return oracledb.connect(**values)
        except Exception as exc:  # noqa: BLE001
            raise PlatformUnavailable(f"Oracle unreachable: {exc}") from exc

    def tables(self) -> list[str]:
        with self._connect() as conn, conn.cursor() as cursor:
            found: list[str] = []
            for owner in self.schemas:
                cursor.execute("SELECT table_name FROM all_tables WHERE owner = UPPER(:o) ORDER BY 1", o=owner)
                found.extend(f"{owner.upper()}.{row[0]}" for row in cursor.fetchall())
        return found

    def columns(self, table: str) -> list[Column]:
        schema, name = split_table(table)
        query = (
            "SELECT column_name, data_type, nullable FROM all_tab_columns "
            "WHERE owner = UPPER(:owner) AND table_name = UPPER(:table_name) ORDER BY column_id"
        )
        with self._connect() as conn, conn.cursor() as cursor:
            cursor.execute(query, owner=schema, table_name=name)
            return [Column(name=str(r[0]), type=str(r[1]), nullable=str(r[2]).upper() == "Y") for r in cursor]

    def dry_run(self, sql: str) -> DryRunResult:
        with self._connect() as conn, conn.cursor() as cursor:
            try:
                cursor.execute(f"EXPLAIN PLAN FOR {sql.rstrip().rstrip(';')}")
            except Exception as exc:  # noqa: BLE001
                return DryRunResult(ok=False, detail=f"{type(exc).__name__}: {exc}")
        return DryRunResult(ok=True, detail="Oracle EXPLAIN PLAN succeeded")

    def query(self, sql: str, max_rows: int = 500) -> list[dict[str, Any]]:
        with self._connect() as conn, conn.cursor() as cursor:
            cursor.execute(sql.rstrip().rstrip(";"))
            names = [d[0] for d in cursor.description]
            return [{n: to_jsonable(v) for n, v in zip(names, row, strict=True)} for row in cursor.fetchmany(max_rows)]


def get_platform(layer: Layer, platform: str) -> Platform:
    """Build the adapter ``config.json`` asks for."""
    cfg = layer.platform_config(platform)
    adapter = cfg.get("adapter", "mock_duckdb")
    dialect = layer.dialect(platform)
    if adapter == "mock_duckdb":
        from semantic_layer.sl.mock_warehouse import require_mock

        require_mock(layer)
        return MockDuckDBPlatform(name=platform, dialect=dialect, path=str(layer.mock_path(platform)))
    if adapter == "bigquery":
        bq = cfg.get("bigquery", {})
        project = os.getenv(bq.get("project_env", "GOOGLE_CLOUD_PROJECT"), "")
        return BigQueryPlatform(name=platform, dialect=dialect, project=project, datasets=list(bq.get("datasets", [])))
    if adapter == "oracle":
        ora = cfg.get("oracle", {})
        return OraclePlatform(
            name=platform,
            dialect=dialect,
            username_env=ora.get("username_env", "ORACLE_USERNAME"),
            password_env=ora.get("password_env", "ORACLE_PASSWORD"),
            dsn_env=ora.get("dsn_env", "ORACLE_DSN"),
            schemas=list(ora.get("schemas", [])),
        )
    raise LayerError(f"Unknown adapter '{adapter}' for platform '{platform}'")


def render(expression: sqlglot.Expression, dialect: str) -> str:
    """Render a sqlglot expression for a platform (e.g. LIMIT vs FETCH FIRST)."""
    return expression.sql(dialect=dialect)
