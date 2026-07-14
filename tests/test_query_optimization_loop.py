"""Tests for the standalone query optimization loop experiment."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from pathlib import Path

from unit_test.query_cost_audit import BYTES_PER_TIB, QueryInput
from unit_test.query_optimization_loop import optimize_query, run_optimization


@dataclass(slots=True)
class _FakeLLMResponse:
    text: str
    error: str = ""


class _SequentialFakeLLMClient:
    """Returns each response in order, then repeats the last one."""

    def __init__(self, responses: list[str]) -> None:
        self.responses = responses
        self.calls = 0

    def complete(self, system_message: str, user_message: str) -> _FakeLLMResponse:
        text = self.responses[min(self.calls, len(self.responses) - 1)]
        self.calls += 1
        return _FakeLLMResponse(text=text)


class _MapEstimator:
    """Deterministic per-SQL-text bytes lookup, keyed exactly."""

    def __init__(self, bytes_by_sql: dict[str, int]) -> None:
        self.bytes_by_sql = bytes_by_sql

    def estimate_bytes(self, sql: str) -> int:
        return self.bytes_by_sql[sql]


class _MapExecutor:
    """Deterministic per-SQL-text row lookup, falling back to a default row set."""

    def __init__(self, rows_by_sql: dict[str, list[dict]] | None = None, default_rows: list[dict] | None = None) -> None:
        self.rows_by_sql = rows_by_sql or {}
        self.default_rows = default_rows if default_rows is not None else [{"a": 1}, {"a": 2}]

    def execute(self, sql: str) -> list[dict]:
        return self.rows_by_sql.get(sql, self.default_rows)


BASELINE_SQL = "SELECT a FROM t"
# price_per_tib chosen equal to BYTES_PER_TIB so estimated_cost_usd == bytes_processed,
# making test cost trajectories directly readable as plain numbers.
_PRICE_PER_TIB = float(BYTES_PER_TIB)


def _query(sql: str = BASELINE_SQL) -> QueryInput:
    return QueryInput(query_id=1, order=1, statement_type="DML", sql=sql)


def test_validation_failure_rolls_back_rather_than_being_adopted() -> None:
    """A candidate whose result set differs must not replace the accepted baseline."""
    candidate_sql = "SELECT a FROM t WHERE a = 1"
    estimator = _MapEstimator({BASELINE_SQL: 100, candidate_sql: 50})
    executor = _MapExecutor(
        rows_by_sql={BASELINE_SQL: [{"a": 1}, {"a": 2}], candidate_sql: [{"a": 1}]},
    )
    llm_client = _SequentialFakeLLMClient([candidate_sql])

    result = optimize_query(
        _query(),
        estimator=estimator,
        executor=executor,
        price_per_tib=_PRICE_PER_TIB,
        llm_client=llm_client,
        llm_config={},
        max_iterations=5,
        min_improvement_pct=2.0,
        diminishing_returns_streak=2,
    )

    assert result.stop_reason == "validation_failed"
    assert result.final_sql == BASELINE_SQL
    assert result.final_cost_usd == result.baseline_cost_usd
    assert len(result.iterations) == 2
    assert result.iterations[1].accepted is False
    assert result.iterations[1].validation_matched is False


def test_diminishing_returns_stops_after_consecutive_small_improvements() -> None:
    """Marginal improvement below the threshold for N consecutive iterations stops the loop."""
    candidate_1 = "SELECT a FROM t /* v1 */"
    candidate_2 = "SELECT a FROM t /* v2 */"
    candidate_3 = "SELECT a FROM t /* v3 */"
    estimator = _MapEstimator(
        {BASELINE_SQL: 100, candidate_1: 90, candidate_2: 89, candidate_3: 88}
    )
    executor = _MapExecutor()  # same default rows for every SQL text -> always validates
    llm_client = _SequentialFakeLLMClient([candidate_1, candidate_2, candidate_3])

    result = optimize_query(
        _query(),
        estimator=estimator,
        executor=executor,
        price_per_tib=_PRICE_PER_TIB,
        llm_client=llm_client,
        llm_config={},
        max_iterations=5,
        min_improvement_pct=5.0,
        diminishing_returns_streak=2,
    )

    # iter 1: 100 -> 90 is a 10% improvement (>= 5%), resets the streak.
    # iter 2: 90 -> 89 is ~1.1% (< 5%), streak = 1.
    # iter 3: 89 -> 88 is ~1.1% (< 5%), streak = 2 -> stop.
    assert result.stop_reason == "diminishing_returns"
    assert len(result.iterations) == 4  # baseline + 3 accepted attempts
    assert result.final_cost_usd == 88
    assert all(it.accepted for it in result.iterations[1:])


def test_max_iterations_cap_triggers_when_improvements_stay_above_threshold() -> None:
    """A cap must stop the loop even if every candidate keeps clearing the improvement bar."""
    candidate_1 = "SELECT a FROM t /* v1 */"
    candidate_2 = "SELECT a FROM t /* v2 */"
    candidate_3 = "SELECT a FROM t /* v3 */"
    estimator = _MapEstimator(
        {BASELINE_SQL: 100, candidate_1: 50, candidate_2: 25, candidate_3: 12}
    )
    executor = _MapExecutor()
    llm_client = _SequentialFakeLLMClient([candidate_1, candidate_2, candidate_3])

    result = optimize_query(
        _query(),
        estimator=estimator,
        executor=executor,
        price_per_tib=_PRICE_PER_TIB,
        llm_client=llm_client,
        llm_config={},
        max_iterations=3,
        min_improvement_pct=1.0,
        diminishing_returns_streak=10,
    )

    assert result.stop_reason == "max_iterations"
    assert len(result.iterations) == 4  # baseline + exactly 3 attempts, never a 4th
    assert result.final_cost_usd == 12
    assert result.total_improvement_pct > 0


def test_run_optimization_end_to_end_rolls_back_worse_candidate_and_writes_report(tmp_path: Path) -> None:
    """A full config-driven run against a real SQLite executor should write a report and never adopt a worse candidate."""
    db_path = tmp_path / "mock_bigquery.db"
    with sqlite3.connect(db_path) as conn:
        conn.execute("CREATE TABLE raw_sales_orders (customer_id INTEGER, amount REAL)")
        conn.executemany(
            "INSERT INTO raw_sales_orders VALUES (?, ?)",
            [(1, 10.0), (1, 20.0), (2, 5.0)],
        )
        conn.commit()

    run_report_path = tmp_path / "run_report.json"
    run_report_path.write_text(
        json.dumps(
            {
                "script_id": "demo-run",
                "units": [
                    {
                        "id": 1,
                        "order": 1,
                        "statement_type": "DML",
                        "bq_sql": "SELECT customer_id, amount FROM raw_sales_orders",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    report_path = tmp_path / "reports" / "optimization_report.md"
    config_path = tmp_path / "query_optimization_loop_config.json"
    config_path.write_text(
        json.dumps(
            {
                "input": {"path": str(run_report_path), "format": "run_report"},
                # A longer candidate always costs more under the mock estimator, so any
                # LLM rewrite here is guaranteed non-improving and must be rejected.
                "estimator": {"mode": "mock", "bytes_per_char": 100},
                "executor": {"mode": "sqlite", "db_path": str(db_path)},
                "pricing": {"price_per_tib_usd": 6.25},
                "optimization": {"max_iterations": 2, "min_improvement_pct": 1.0, "diminishing_returns_streak": 2},
                "llm": {"enabled": True},
                "output": {"report_md": str(report_path)},
            }
        ),
        encoding="utf-8",
    )

    fake_client = _SequentialFakeLLMClient(
        ["SELECT customer_id, amount FROM raw_sales_orders WHERE amount > 0"]
    )
    results = run_optimization(config_path, llm_client=fake_client)

    assert len(results) == 1
    assert results[0].stop_reason == "diminishing_returns"
    assert results[0].final_sql == "SELECT customer_id, amount FROM raw_sales_orders"
    assert report_path.exists()
    report_text = report_path.read_text(encoding="utf-8")
    assert "Query Optimization Loop — demo-run" in report_text
    assert "Stop reason: diminishing_returns" in report_text


def test_run_optimization_skips_llm_when_disabled(tmp_path: Path) -> None:
    """Disabling the LLM pass should leave every query at its baseline with zero attempts."""
    run_report_path = tmp_path / "run_report.json"
    run_report_path.write_text(
        json.dumps(
            {
                "script_id": "demo-run",
                "units": [{"id": 1, "order": 1, "statement_type": "DML", "bq_sql": "SELECT 1"}],
            }
        ),
        encoding="utf-8",
    )
    report_path = tmp_path / "reports" / "optimization_report.md"
    config_path = tmp_path / "config.json"
    config_path.write_text(
        json.dumps(
            {
                "input": {"path": str(run_report_path), "format": "run_report"},
                "estimator": {"mode": "mock"},
                "executor": {"mode": "sqlite", "db_path": str(tmp_path / "empty.db")},
                "llm": {"enabled": False},
                "output": {"report_md": str(report_path)},
            }
        ),
        encoding="utf-8",
    )

    results = run_optimization(config_path)

    assert len(results) == 1
    assert results[0].stop_reason == "max_iterations"
    assert len(results[0].iterations) == 1
    assert results[0].final_sql == "SELECT 1"
