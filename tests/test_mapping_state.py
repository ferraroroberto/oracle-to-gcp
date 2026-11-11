"""Table-mapping readiness: one derive function, one row per observation, and the preflight readers."""

from __future__ import annotations

import sqlite3

import pytest

from src.mock_environment import bootstrap_mock_environment
from src.pipeline_config import load_pipeline_config
from src.preflight import _table_reachable, run_table_preflight
from src.sql_processing import build_units
from src.sqlite_db import connect_sqlite
from src.table_registry import (
    HOLD_STATUS,
    MappingReason,
    MappingState,
    TableMapping,
    derive_mapping_state,
    get_mapping,
    update_reachability,
    upsert_mapping,
)

STAMP = "2026-01-01T00:00:00+00:00"


def _mapping(**overrides: object) -> TableMapping:
    base: dict[str, object] = {
        "oracle_schema": "demo",
        "oracle_table": "sales_orders",
        "bigquery_table": "raw_sales_orders",
    }
    base.update(overrides)
    return TableMapping(**base)  # type: ignore[arg-type]


# (id, mapping facts, probe failures, expected state, expected reasons)
OBSERVATIONS = [
    ("seeded-never-probed", {"status": "ready"}, {}, MappingState.UNCHECKED, ()),
    ("pending-never-probed", {"status": "pending"}, {}, MappingState.UNCHECKED, ()),
    ("ready-without-bigquery-table", {"status": "ready", "bigquery_table": ""}, {}, MappingState.UNMAPPED, ()),
    (
        "ready-both-probed-reachable",
        {"status": "ready", "oracle_reachable": True, "bigquery_reachable": True},
        {},
        MappingState.READY,
        (),
    ),
    (
        "pending-both-probed-reachable",
        {"status": "pending", "oracle_reachable": True, "bigquery_reachable": True},
        {},
        MappingState.READY,
        (),
    ),
    (
        "operator-hold-both-reachable",
        {"status": HOLD_STATUS, "oracle_reachable": True, "bigquery_reachable": True},
        {},
        MappingState.BLOCKED,
        (MappingReason.HELD,),
    ),
    (
        "both-unreachable-after-probe",
        {"status": "pending", "oracle_checked_at": STAMP, "bigquery_checked_at": STAMP},
        {},
        MappingState.BLOCKED,
        (MappingReason.ORACLE_UNREACHABLE, MappingReason.BIGQUERY_UNREACHABLE),
    ),
    (
        "bigquery-table-disappeared",
        {"status": "ready", "oracle_reachable": True, "bigquery_checked_at": STAMP},
        {},
        MappingState.BLOCKED,
        (MappingReason.BIGQUERY_UNREACHABLE,),
    ),
    (
        "oracle-reachable-bigquery-never-probed",
        {"status": "ready", "oracle_reachable": True},
        {},
        MappingState.UNCHECKED,
        (),
    ),
    (
        "probe-could-not-run-on-a-stored-reachable-side",
        {"status": "ready", "oracle_reachable": True, "bigquery_reachable": True},
        {"bigquery_probe_failed": True},
        MappingState.UNKNOWN,
        (MappingReason.BIGQUERY_PROBE_FAILED,),
    ),
    (
        "probe-could-not-run-on-both-sides",
        {"status": "ready", "oracle_reachable": True, "bigquery_reachable": True},
        {"oracle_probe_failed": True, "bigquery_probe_failed": True},
        MappingState.UNKNOWN,
        (MappingReason.ORACLE_PROBE_FAILED, MappingReason.BIGQUERY_PROBE_FAILED),
    ),
    (
        "known-no-outranks-probe-that-could-not-run",
        {"status": "ready", "oracle_checked_at": STAMP, "bigquery_reachable": True},
        {"bigquery_probe_failed": True},
        MappingState.BLOCKED,
        (MappingReason.ORACLE_UNREACHABLE,),
    ),
    ("hold-on-unmapped-row-stays-unmapped", {"status": HOLD_STATUS, "bigquery_table": ""}, {}, MappingState.UNMAPPED, ()),
]


@pytest.mark.parametrize(
    ("facts", "probes", "state", "reasons"),
    [pytest.param(f, p, s, r, id=name) for name, f, p, s, r in OBSERVATIONS],
)
def test_derive_mapping_state_table(facts: dict, probes: dict, state: MappingState, reasons: tuple) -> None:
    derived = derive_mapping_state(_mapping(**facts), **probes)

    assert derived.state is state
    assert derived.reasons == reasons
    assert derived.is_ready is (state is MappingState.READY)
    if not probes:
        assert _mapping(**facts).ready is (state is MappingState.READY)


def test_describe_names_the_state_and_its_reasons() -> None:
    held = derive_mapping_state(_mapping(status=HOLD_STATUS, oracle_reachable=True, bigquery_reachable=True))

    assert held.describe() == "blocked (held)"
    assert derive_mapping_state(_mapping()).describe() == "unchecked"


def test_a_hold_is_not_a_ready_state_even_though_both_sides_probe_reachable() -> None:
    """state-machine-proof: table-mapping

    The illegal transition: a probe that finds both sides reachable must not lift an operator hold.
    """
    held = _mapping(status=HOLD_STATUS)
    held.oracle_reachable = held.bigquery_reachable = True

    assert not derive_mapping_state(held).is_ready
    assert not held.ready


def test_probing_does_not_rewrite_the_operator_hold(tmp_path) -> None:
    registry = tmp_path / "registry.db"
    upsert_mapping(_mapping(status=HOLD_STATUS), registry)

    updated = update_reachability("demo", "sales_orders", oracle_reachable=True, bigquery_reachable=True, path=registry)

    assert updated.status == HOLD_STATUS
    assert get_mapping("demo", "sales_orders", registry).status == HOLD_STATUS
    assert not updated.ready


def test_probing_does_not_promote_or_demote_other_stored_status(tmp_path) -> None:
    registry = tmp_path / "registry.db"
    upsert_mapping(_mapping(status="pending"), registry)

    promoted = update_reachability("demo", "sales_orders", oracle_reachable=True, bigquery_reachable=True, path=registry)
    assert promoted.status == "pending"
    assert promoted.ready

    demoted = update_reachability("demo", "sales_orders", bigquery_reachable=False, path=registry)
    assert demoted.status == "pending"
    assert derive_mapping_state(demoted).reasons == (MappingReason.BIGQUERY_UNREACHABLE,)


def test_table_probe_tells_absent_from_could_not_run() -> None:
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE present (id INTEGER)")

    assert _table_reachable(conn, "present") is True
    assert _table_reachable(conn, "absent") is False
    assert _table_reachable(conn, "bad name with spaces") is None
    conn.close()


def _preflight(tmp_path, registry, script: str, oracle_conn, bigquery_conn):
    config = load_pipeline_config()
    units = build_units(script)
    return run_table_preflight(
        units=units,
        pipeline_config=config,
        legacy_mapping={},
        oracle_conn=oracle_conn,
        bigquery_conn=bigquery_conn,
        registry_path=str(registry),
    )


def _seed(registry, **overrides: object) -> None:
    upsert_mapping(
        _mapping(oracle_schema=load_pipeline_config().oracle.default_schema, **overrides),
        registry,
    )


def test_preflight_blocks_a_held_mapping_and_names_the_reason(tmp_path) -> None:
    paths = bootstrap_mock_environment(tmp_path)
    registry = tmp_path / "registry.db"
    _seed(registry, status=HOLD_STATUS)

    with connect_sqlite(paths["oracle_db"]) as oracle_conn, connect_sqlite(paths["bigquery_db"]) as bq_conn:
        result = _preflight(tmp_path, registry, "SELECT * FROM sales_orders;", oracle_conn, bq_conn)

    assert not result.can_run
    assert result.blocked_tables == ["sales_orders"]
    assert result.messages == ["sales_orders: blocked (held)"]


def test_preflight_reprobes_a_stored_reachable_flag(tmp_path) -> None:
    paths = bootstrap_mock_environment(tmp_path)
    registry = tmp_path / "registry.db"
    _seed(registry, status="ready", oracle_reachable=True, bigquery_reachable=True, bigquery_table="gone_table")

    with connect_sqlite(paths["oracle_db"]) as oracle_conn, connect_sqlite(paths["bigquery_db"]) as bq_conn:
        result = _preflight(tmp_path, registry, "SELECT * FROM sales_orders;", oracle_conn, bq_conn)

    assert not result.can_run
    assert result.messages == ["sales_orders: blocked (bigquery_unreachable)"]
    stored = get_mapping(load_pipeline_config().oracle.default_schema, "sales_orders", registry)
    assert stored.bigquery_reachable is False
    assert stored.bigquery_checked_at


def test_preflight_reports_unknown_when_a_probe_could_not_run(tmp_path) -> None:
    paths = bootstrap_mock_environment(tmp_path)
    registry = tmp_path / "registry.db"
    _seed(registry, status="ready", oracle_reachable=True, bigquery_reachable=True, bigquery_table="bad-name")

    with connect_sqlite(paths["oracle_db"]) as oracle_conn, connect_sqlite(paths["bigquery_db"]) as bq_conn:
        result = _preflight(tmp_path, registry, "SELECT * FROM sales_orders;", oracle_conn, bq_conn)

    assert not result.can_run
    assert result.messages == ["sales_orders: unknown (bigquery_probe_failed)"]
