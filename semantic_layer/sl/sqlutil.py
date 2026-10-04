"""Small sqlglot helpers shared by harvest, validation, compose and lint.

Definitions store SQL *fragments* with placeholders: ``{t}`` (the table's
alias), ``{from}`` / ``{to}`` (the two sides of a relationship) and
``{entity.key}`` (in metric expressions). These helpers substitute them and
compare fragments structurally rather than as raw text.
"""

from __future__ import annotations

import re

import sqlglot
from sqlglot import exp

PLACEHOLDER = re.compile(r"\{(t|from|to|entity\.key)\}")


def substitute(fragment: str, **aliases: str) -> str:
    """Replace ``{t}``/``{from}``/``{to}``/``{entity.key}`` placeholders."""
    mapping = {key.replace("entity_key", "entity.key"): value for key, value in aliases.items()}
    return PLACEHOLDER.sub(lambda m: mapping.get(m.group(1), m.group(0)), fragment)


def _as_condition(fragment: str, dialect: str) -> exp.Expression:
    text = substitute(fragment, t="t", **{"from": "f", "to": "o"}, entity_key="t.k")
    return sqlglot.parse_one(f"SELECT 1 FROM x AS t WHERE {text}", read=dialect).args["where"].this


def _as_value(fragment: str, dialect: str) -> exp.Expression:
    text = substitute(fragment, t="t", entity_key="t.k")
    return sqlglot.parse_one(f"SELECT {text} FROM x AS t", read=dialect).expressions[0]


def fragment_columns(fragment: str, dialect: str, predicate: bool = True) -> set[str]:
    """Lower-cased column names a fragment reads (placeholders excluded)."""
    node = _as_condition(fragment, dialect) if predicate else _as_value(fragment, dialect)
    return {c.name.lower() for c in node.find_all(exp.Column) if c.name.lower() != "k"}


def split_conjuncts(node: exp.Expression | None) -> list[exp.Expression]:
    """Top-level AND terms of a predicate."""
    if node is None:
        return []
    if isinstance(node, exp.Paren):
        return split_conjuncts(node.this)
    if isinstance(node, exp.And):
        return split_conjuncts(node.left) + split_conjuncts(node.right)
    return [node]


def unqualified(node: exp.Expression) -> exp.Expression:
    """Copy of ``node`` with table qualifiers stripped and identifiers lower-cased."""
    copy = node.copy()
    for column in copy.find_all(exp.Column):
        column.set("table", None)
        column.set("db", None)
        column.set("this", exp.to_identifier(column.name.lower()))
    return copy


def canonical(node: exp.Expression) -> str:
    """Dialect-neutral text for comparing predicates (rendered as DuckDB SQL)."""
    return unqualified(node).sql(dialect="duckdb")


def predicate_set(fragment: str, dialect: str) -> list[str]:
    """Canonical conjuncts of a filter fragment, in source order."""
    return [canonical(term) for term in split_conjuncts(_as_condition(fragment, dialect))]


def literals(node: exp.Expression) -> list[str]:
    """String/number literals inside a predicate (where tribal knowledge hides)."""
    values: list[str] = []
    for lit in node.find_all(exp.Literal):
        values.append(lit.this if lit.is_string else str(lit.this))
    return values


def normalise_sql(sql: str) -> str:
    """Whitespace/case-insensitive form for containment checks."""
    return re.sub(r"\s+", " ", sql).strip().lower()


def transpile(sql: str, read: str, write: str) -> str:
    return ";\n".join(sqlglot.transpile(sql, read=read, write=write))
