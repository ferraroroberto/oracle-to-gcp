"""Deterministic SQL preparation helpers for the demo pipeline."""

from __future__ import annotations

import re
import sqlite3
from collections.abc import Iterable

from src.sql_models import SqlUnit

_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_SELECT_INTO_RE = re.compile(
    r"SELECT\s+(?P<expr>.+?)\s+INTO\s+(?P<var>[a-zA-Z_][\w$]*)\s+"
    r"FROM\s+(?P<table>[a-zA-Z_][\w$]*)(?P<tail>.*?);",
    re.IGNORECASE | re.DOTALL,
)
_PLSQL_BLOCK_RE = re.compile(r"\bDECLARE\b.*?\bEND\s*;\s*/", re.IGNORECASE | re.DOTALL)
_SOURCE_RE = re.compile(r"\b(?:FROM|JOIN)\s+([`\"]?[a-zA-Z_][\w$.]*[`\"]?)", re.IGNORECASE)
_TARGET_RE = re.compile(
    r"\bCREATE\s+(?:OR\s+REPLACE\s+)?TABLE\s+([`\"]?[a-zA-Z_][\w$.]*[`\"]?)",
    re.IGNORECASE,
)


def materialize_variables(script: str, oracle_conn: sqlite3.Connection) -> tuple[str, dict[str, str]]:
    """Resolve simple SELECT-INTO variables and return pure SQL."""
    resolved: dict[str, str] = {}

    for match in _SELECT_INTO_RE.finditer(script):
        expr = " ".join(match.group("expr").split())
        table = _clean_identifier(match.group("table"))
        tail = match.group("tail").strip()
        query = f"SELECT {expr} FROM {table} {tail}".strip()
        value = oracle_conn.execute(_oracle_to_sqlite_runtime(query)).fetchone()[0]
        literal = _to_sql_literal(value)
        resolved[match.group("var").lower()] = literal

    pure_sql = _PLSQL_BLOCK_RE.sub("", script)
    for name, literal in resolved.items():
        pure_sql = re.sub(rf"\b{re.escape(name)}\b", literal, pure_sql, flags=re.IGNORECASE)
    return pure_sql.strip(), resolved


def split_sql_units(pure_sql: str) -> list[str]:
    """Split SQL statements on semicolons outside quoted strings."""
    statements: list[str] = []
    current: list[str] = []
    in_single_quote = False
    i = 0
    while i < len(pure_sql):
        char = pure_sql[i]
        current.append(char)
        if char == "'":
            next_char = pure_sql[i + 1] if i + 1 < len(pure_sql) else ""
            if in_single_quote and next_char == "'":
                current.append(next_char)
                i += 1
            else:
                in_single_quote = not in_single_quote
        elif char == ";" and not in_single_quote:
            statement = "".join(current).strip().rstrip(";").strip()
            if statement:
                statements.append(statement)
            current = []
        i += 1

    tail = "".join(current).strip()
    if tail:
        statements.append(tail)
    return statements


def build_units(pure_sql: str) -> list[SqlUnit]:
    """Build ordered unit records from pure SQL."""
    units: list[SqlUnit] = []
    for index, statement in enumerate(split_sql_units(pure_sql), start=1):
        units.append(
            SqlUnit(
                id=index,
                order=index,
                raw_oracle=statement,
                pure_oracle=statement,
                statement_type=classify_statement(statement),
                sources=extract_sources(statement),
                targets=extract_targets(statement),
            )
        )
    return units


def classify_statement(statement: str) -> str:
    """Return a small statement class used by the UI and runner."""
    stripped = statement.lstrip().upper()
    if stripped.startswith("CREATE"):
        return "DDL"
    if stripped.startswith(("INSERT", "UPDATE", "DELETE", "MERGE")):
        return "DML"
    if stripped.startswith("SELECT"):
        return "SELECT"
    return "SQL"


def extract_sources(statement: str) -> list[str]:
    """Extract table names read by a statement."""
    return _dedupe(_clean_identifier(match) for match in _SOURCE_RE.findall(statement))


def extract_targets(statement: str) -> list[str]:
    """Extract table names written by a statement."""
    return _dedupe(_clean_identifier(match) for match in _TARGET_RE.findall(statement))


def oracle_sqlite_runtime_sql(statement: str) -> str:
    """Convert supported Oracle-ish SQL to executable SQLite SQL."""
    return _oracle_to_sqlite_runtime(statement)


def bigquery_sqlite_runtime_sql(statement: str) -> list[str]:
    """Convert supported BigQuery-ish SQL to executable SQLite SQL statements."""
    sql = _oracle_to_sqlite_runtime(statement.replace("`", ""))
    match = re.match(
        r"\s*CREATE\s+OR\s+REPLACE\s+TABLE\s+([a-zA-Z_][\w$]*)\s+AS\s+(?P<select>.*)",
        sql,
        flags=re.IGNORECASE | re.DOTALL,
    )
    if not match:
        return [sql]
    target = match.group(1)
    return [f"DROP TABLE IF EXISTS {target}", f"CREATE TABLE {target} AS {match.group('select')}"]


def _oracle_to_sqlite_runtime(statement: str) -> str:
    sql = re.sub(r"\bDATE\s+'([^']+)'", r"'\1'", statement, flags=re.IGNORECASE)
    sql = re.sub(r"\bTRUNC\s*\(", "date(", sql, flags=re.IGNORECASE)
    sql = re.sub(r"\bNVL\s*\(", "COALESCE(", sql, flags=re.IGNORECASE)
    return sql


def _to_sql_literal(value: object) -> str:
    if value is None:
        return "NULL"
    text = str(value)
    escaped = text.replace("'", "''")
    if _DATE_RE.match(text):
        return f"DATE '{escaped}'"
    return f"'{escaped}'"


def _clean_identifier(identifier: str) -> str:
    return identifier.strip("`\"").split(".")[-1].lower()


def _dedupe(values: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        if value not in seen:
            seen.add(value)
            result.append(value)
    return result
