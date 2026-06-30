"""Data records used by the Oracle-to-BigQuery mock pipeline."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(slots=True)
class SqlUnit:
    """One ordered SQL statement after PL/SQL materialization."""

    id: int
    order: int
    raw_oracle: str
    pure_oracle: str
    statement_type: str
    sources: list[str] = field(default_factory=list)
    targets: list[str] = field(default_factory=list)
    bq_sql: str = ""
    status: str = "pending"
    validation_result: dict[str, Any] = field(default_factory=dict)
    repair_attempts: int = 0
    translator: str = ""


@dataclass(slots=True)
class RunReport:
    """Serializable summary of a complete translation run."""

    script_id: str
    status: str
    resolved_variables: dict[str, str]
    units: list[SqlUnit]
    final_bigquery_script: str
    artifacts: dict[str, str]
    log: list[str] = field(default_factory=list)
