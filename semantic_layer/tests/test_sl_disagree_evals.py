"""`sl disagree` (two-team) and `sl eval` (golden questions, including planted failures)."""

from __future__ import annotations

from pathlib import Path

import yaml

from semantic_layer.sl import disagree, evals
from semantic_layer.sl.common import Layer


def test_two_team_disagreement_has_numbers(layer: Layer) -> None:
    report = disagree.run(layer)
    assert len(report["disagreements"]) == 1
    item = report["disagreements"][0]
    assert item["table"] == "analytics.customer_monthly"
    counts = {v["team"]: v["count"] for v in item["variants"]}
    assert counts == {"core_analytics": 446, "marketing": 398}
    assert item["spread"] == 48


def test_golden_questions_pass(layer: Layer) -> None:
    report = evals.run(layer)
    assert report["questions"] == 15
    assert report["passed"], [r for r in report["results"] if not r["passed"]]
    assert all(score == 1.0 for score in report["scores"].values())


def _edit(path: Path, change) -> None:
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    change(data)
    path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")


def test_eval_scores_drop_on_a_template_regression(layer: Layer, layer_root: Path) -> None:
    _edit(layer_root / "domains/core_analytics/templates/tpl.active_customers_by_store_and_join_month.yaml",
          lambda d: d.update(status="reviewed"))
    report = evals.run(layer)
    assert report["scores"]["template_accuracy"] < 1.0
    failed = [r["question"] for r in report["results"] if not r["passed"]]
    assert failed == ["active customers by home store and join month for August 2026"]


def test_eval_gate_fails_when_resolution_regresses(layer: Layer, layer_root: Path) -> None:
    """Dropping the synonym most questions rely on must fail the gate (thresholds are not decorative)."""
    _edit(layer_root / "domains/core_analytics/entities/customer.active_buyer.yaml",
          lambda d: d.update(name="Twelve-month buyer",
                             synonyms=[s for s in d["synonyms"] if s != "active customer"]))
    report = evals.run(layer)
    assert not report["passed"]
    assert "resolution_accuracy" in report["failing_metrics"]
