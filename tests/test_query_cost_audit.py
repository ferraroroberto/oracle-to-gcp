"""Tests for the standalone query cost audit experiment."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from unit_test.query_cost_audit import run_audit


@dataclass(slots=True)
class _FakeLLMResponse:
    text: str
    error: str = ""


class _FakeLLMClient:
    """Stub local hub client so tests never make a real network call."""

    def __init__(self, response_text: str = "- Add clustering on customer_id.") -> None:
        self.response_text = response_text
        self.calls: list[tuple[str, str]] = []

    def complete(self, system_message: str, user_message: str) -> _FakeLLMResponse:
        self.calls.append((system_message, user_message))
        return _FakeLLMResponse(text=self.response_text)


def _write_run_report(path: Path) -> None:
    path.write_text(
        json.dumps(
            {
                "script_id": "demo-run",
                "units": [
                    {
                        "id": 1,
                        "order": 1,
                        "statement_type": "DDL",
                        "bq_sql": "CREATE OR REPLACE TABLE t AS SELECT * FROM raw_sales_orders",
                    },
                    {
                        "id": 2,
                        "order": 2,
                        "statement_type": "DML",
                        "bq_sql": "SELECT customer_id FROM raw_sales_orders WHERE amount > 0",
                    },
                    {
                        "id": 3,
                        "order": 3,
                        "statement_type": "DML",
                        "bq_sql": "",
                    },
                ],
            }
        ),
        encoding="utf-8",
    )


def test_query_cost_audit_ranks_by_estimated_cost_and_writes_report(tmp_path: Path) -> None:
    """The mock estimator should rank the longer statement as more expensive."""
    run_report_path = tmp_path / "run_report.json"
    _write_run_report(run_report_path)

    report_path = tmp_path / "reports" / "cost_report.md"
    config_path = tmp_path / "query_cost_audit_config.json"
    config_path.write_text(
        json.dumps(
            {
                "input": {"path": str(run_report_path), "format": "run_report"},
                "estimator": {"mode": "mock", "bytes_per_char": 1},
                "pricing": {"price_per_tib_usd": 6.25},
                "llm": {"enabled": True},
                "output": {"report_md": str(report_path)},
            }
        ),
        encoding="utf-8",
    )

    fake_client = _FakeLLMClient()
    estimates = run_audit(config_path, llm_client=fake_client)

    # Empty bq_sql (unit 3) must be excluded — nothing to cost.
    assert len(estimates) == 2
    # The longer DDL statement scans more (mock) bytes than the shorter SELECT.
    assert estimates[0].statement_type == "DDL"
    assert estimates[0].rank == 1
    assert estimates[0].estimated_bytes_processed > estimates[1].estimated_bytes_processed
    assert estimates[0].estimated_cost_usd >= estimates[1].estimated_cost_usd

    assert len(fake_client.calls) == 1
    assert report_path.exists()
    report_text = report_path.read_text(encoding="utf-8")
    assert "Query Cost Audit — demo-run" in report_text
    assert "Add clustering on customer_id" in report_text
    assert "Total estimated cost" in report_text


def test_query_cost_audit_skips_llm_when_disabled(tmp_path: Path) -> None:
    """Disabling the LLM pass should not call the client and note the skip in the report."""
    run_report_path = tmp_path / "run_report.json"
    _write_run_report(run_report_path)

    report_path = tmp_path / "reports" / "cost_report.md"
    config_path = tmp_path / "query_cost_audit_config.json"
    config_path.write_text(
        json.dumps(
            {
                "input": {"path": str(run_report_path), "format": "run_report"},
                "estimator": {"mode": "mock"},
                "llm": {"enabled": False},
                "output": {"report_md": str(report_path)},
            }
        ),
        encoding="utf-8",
    )

    fake_client = _FakeLLMClient()
    run_audit(config_path, llm_client=fake_client)

    assert fake_client.calls == []
    report_text = report_path.read_text(encoding="utf-8")
    assert "No optimization suggestions generated" in report_text


def test_query_cost_audit_records_estimator_errors(tmp_path: Path) -> None:
    """An estimator failure should still produce a report row instead of crashing the run."""

    class _BrokenEstimator:
        def estimate_bytes(self, sql: str) -> int:
            raise RuntimeError("dry-run failed")

    run_report_path = tmp_path / "run_report.json"
    _write_run_report(run_report_path)

    report_path = tmp_path / "reports" / "cost_report.md"
    config_path = tmp_path / "query_cost_audit_config.json"
    config_path.write_text(
        json.dumps(
            {
                "input": {"path": str(run_report_path), "format": "run_report"},
                "estimator": {"mode": "mock"},
                "llm": {"enabled": False},
                "output": {"report_md": str(report_path)},
            }
        ),
        encoding="utf-8",
    )

    import unit_test.query_cost_audit as module

    original_build_estimator = module.build_estimator
    module.build_estimator = lambda config: _BrokenEstimator()
    try:
        estimates = run_audit(config_path)
    finally:
        module.build_estimator = original_build_estimator

    assert all(estimate.error for estimate in estimates)
    assert all(estimate.estimated_cost_usd == 0 for estimate in estimates)
