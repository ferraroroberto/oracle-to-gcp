"""Data records used by the Oracle-to-BigQuery mock pipeline."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


class UnitStatus(StrEnum):
    """Where one SQL unit stands. `pending` resolves once, to `validated` or `flagged`."""

    PENDING = "pending"
    VALIDATED = "validated"
    FLAGGED = "flagged"


class RunStatus(StrEnum):
    """Outcome of a whole run: units validated, units flagged for review, or the run raised."""

    VALIDATED = "validated"
    FLAGGED = "flagged"
    FAILED = "failed"


class IllegalStatusTransition(ValueError):
    """A unit was moved to a status its current one does not allow."""


# A resolved unit is final: the repair loop returns on the first fingerprint match and flags
# once, so a later move would contradict the report already derived from it.
_UNIT_TRANSITIONS: dict[UnitStatus, frozenset[UnitStatus]] = {
    UnitStatus.PENDING: frozenset({UnitStatus.VALIDATED, UnitStatus.FLAGGED}),
    UnitStatus.VALIDATED: frozenset(),
    UnitStatus.FLAGGED: frozenset(),
}


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
    status: UnitStatus = UnitStatus.PENDING
    validation_result: dict[str, Any] = field(default_factory=dict)
    repair_attempts: int = 0
    translator: str = ""

    def transition(self, target: UnitStatus) -> None:
        """Move to `target`, or raise when the current status does not allow it."""
        if target not in _UNIT_TRANSITIONS[self.status]:
            raise IllegalStatusTransition(f"unit {self.id}: {self.status} -> {target} is not allowed")
        self.status = target


@dataclass(slots=True)
class RunReport:
    """Serializable summary of a complete translation run."""

    script_id: str
    status: RunStatus
    resolved_variables: dict[str, str]
    units: list[SqlUnit]
    final_bigquery_script: str
    artifacts: dict[str, str]
    log: list[str] = field(default_factory=list)
    trace: list[dict[str, Any]] = field(default_factory=list)


def derive_run_status(units: Iterable[SqlUnit]) -> RunStatus:
    """The one place unit statuses become a run status; a unit still pending counts as unvalidated."""
    return RunStatus.VALIDATED if all(unit.status is UnitStatus.VALIDATED for unit in units) else RunStatus.FLAGGED
