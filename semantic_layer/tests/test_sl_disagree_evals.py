"""`sl disagree` (two-team) and `sl eval` (golden questions, including planted failures)."""

from __future__ import annotations

from pathlib import Path

import yaml

from semantic_layer.sl import disagree, evals
from semantic_layer.sl.common import Layer
from semantic_layer.tests.conftest import execute


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


def _rename_entity_key(layer_root: Path) -> None:
    """Port the pilot to a layer whose entity key is `member_id`, not `*customer_id`."""
    for session in ("2026-10-03-core-analytics", "2026-10-10-marketing"):
        folder = layer_root / "harvest" / "sessions" / session
        for name in ("parse.json", "answers.yaml"):
            text = (folder / name).read_text(encoding="utf-8")
            text = text.replace("customer_id", "member_id").replace("CUSTOMER_ID", "MEMBER_ID")
            (folder / name).write_text(text, encoding="utf-8")
    execute(layer_root, "legacy", "ALTER TABLE DWH.CUSTOMER_MONTHLY RENAME COLUMN CUSTOMER_ID TO MEMBER_ID")
    execute(layer_root, "cloud", "ALTER TABLE analytics.customer_monthly RENAME COLUMN customer_id TO member_id")


def test_population_key_comes_from_the_declared_entity_key(layer: Layer, layer_root: Path) -> None:
    """A key not named `*customer_id` must still be counted as distinct entities, never `COUNT(*)`."""
    _rename_entity_key(layer_root)
    item = disagree.run(layer)["disagreements"][0]
    assert {v["team"]: v["count"] for v in item["variants"]} == {"core_analytics": 446, "marketing": 398}
    details = {v["team"]: v["detail"] for v in item["variants"]}
    assert details["marketing"].startswith("COUNT(DISTINCT cm.member_id)")  # declared key, matched by alias
    assert details["core_analytics"].startswith("COUNT(DISTINCT cm.MEMBER_ID)")  # CTE alias: join-column fallback
