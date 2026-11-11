"""Translation-run status: one derive function, a closed unit transition table, JSON-stable values."""

from __future__ import annotations

import json
from dataclasses import asdict

import pytest

from src.sql_models import (
    IllegalStatusTransition,
    RunReport,
    RunStatus,
    SqlUnit,
    UnitStatus,
    derive_run_status,
)


def _unit(status: UnitStatus = UnitStatus.PENDING) -> SqlUnit:
    unit = SqlUnit(id=1, order=1, raw_oracle="select 1", pure_oracle="select 1", statement_type="select")
    unit.status = status
    return unit


# (id, unit statuses, expected run status)
RUN_OBSERVATIONS = [
    ("all-validated", [UnitStatus.VALIDATED, UnitStatus.VALIDATED], RunStatus.VALIDATED),
    ("one-flagged", [UnitStatus.VALIDATED, UnitStatus.FLAGGED], RunStatus.FLAGGED),
    ("all-flagged", [UnitStatus.FLAGGED, UnitStatus.FLAGGED], RunStatus.FLAGGED),
    ("a-unit-never-resolved", [UnitStatus.VALIDATED, UnitStatus.PENDING], RunStatus.FLAGGED),
    ("no-units", [], RunStatus.VALIDATED),
]


@pytest.mark.parametrize(("statuses", "expected"), [row[1:] for row in RUN_OBSERVATIONS], ids=[row[0] for row in RUN_OBSERVATIONS])
def test_run_status_is_derived_from_unit_statuses(statuses: list[UnitStatus], expected: RunStatus) -> None:
    assert derive_run_status([_unit(status) for status in statuses]) is expected


def test_a_new_unit_is_pending() -> None:
    assert _unit().status is UnitStatus.PENDING


@pytest.mark.parametrize("target", [UnitStatus.VALIDATED, UnitStatus.FLAGGED])
def test_a_pending_unit_resolves_once(target: UnitStatus) -> None:
    unit = _unit()
    unit.transition(target)
    assert unit.status is target


@pytest.mark.parametrize("resolved", [UnitStatus.VALIDATED, UnitStatus.FLAGGED])
@pytest.mark.parametrize("target", list(UnitStatus))
def test_a_resolved_unit_refuses_every_further_transition(resolved: UnitStatus, target: UnitStatus) -> None:
    """state-machine-proof: translation-run

    The illegal transition: a unit already validated or flagged must not be moved again, so a late
    mismatch cannot turn a validated unit into a flagged one (or the reverse) behind the run status.
    """
    unit = _unit(resolved)
    with pytest.raises(IllegalStatusTransition):
        unit.transition(target)
    assert unit.status is resolved


def test_a_unit_cannot_return_to_pending() -> None:
    unit = _unit()
    with pytest.raises(IllegalStatusTransition):
        unit.transition(UnitStatus.PENDING)


def test_statuses_serialise_as_the_plain_strings_the_reports_always_held() -> None:
    report = RunReport(
        script_id="demo",
        status=RunStatus.FAILED,
        resolved_variables={},
        units=[_unit(UnitStatus.FLAGGED)],
        final_bigquery_script="",
        artifacts={},
    )
    payload = json.loads(json.dumps(asdict(report)))
    assert payload["status"] == "failed"
    assert payload["units"][0]["status"] == "flagged"
    assert {status.value for status in RunStatus} == {"validated", "flagged", "failed"}
    assert {status.value for status in UnitStatus} == {"pending", "validated", "flagged"}
