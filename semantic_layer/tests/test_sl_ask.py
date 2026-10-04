"""`sl ask` — resolution, template-first, compose, lint, reconcile, provenance."""

from __future__ import annotations

from semantic_layer.sl import ask
from semantic_layer.sl.common import Layer
from semantic_layer.sl.model import load_model

GOLD = {"entity": "customer.active_buyer", "metric": "metric.customer_count",
        "dimensions": ["dimension.home_store:region", "dimension.home_store:store", "dimension.join_month"],
        "period": "2026-08"}


def test_resolve_flags_customer_and_store_ambiguity(layer: Layer) -> None:
    result = ask.resolve_text(layer, "how many customers per store")
    assert result["needs_clarification"]
    questions = {c["term"]: {o["id"] for o in c["options"]} for c in result["clarifications"]}
    assert questions["store"] == {"dimension.home_store", "dimension.first_purchase_store"}
    assert "customer.active_buyer" in questions["(population)"]


def test_resolve_unambiguous_terms(layer: Layer) -> None:
    result = ask.resolve_text(layer, ["active customers", "home store", "join month"])
    assert not result["needs_clarification"]
    assert result["entity"] == "customer.active_buyer"
    assert result["metric"] == "metric.customer_count"  # the one certified metric for that entity
    assert set(result["dimensions"]) == {"dimension.home_store", "dimension.join_month"}


def test_resolve_marks_uncertified_matches(layer: Layer) -> None:
    result = ask.resolve_text(layer, ["recent buyers"])
    assert result["entity"] == "customer.campaign_reachable"
    assert result["uncertified"] == ["customer.campaign_reachable"]


def test_gold_question_uses_the_certified_template_and_reconciles(layer: Layer) -> None:
    answer = ask.run(layer, GOLD)
    assert answer["ok"], answer["errors"]
    assert answer["source"].startswith("certified template tpl.active_customers_by_store_and_join_month")
    assert answer["total"] == 446
    assert answer["reconciliation"]["status"] == "reconciles"
    assert answer["lint"]["ok"]
    stewards = {p["id"]: p["steward"] for p in answer["provenance"]}
    assert stewards["customer.active_buyer"] == "avery.lane"


def test_composed_answer_is_lint_clean_and_reconciles(layer: Layer) -> None:
    spec = {"entity": "customer.active_buyer", "metric": "metric.customer_count",
            "dimensions": ["dimension.first_purchase_store:region"], "period": "2026-09"}
    answer = ask.run(layer, spec)
    assert answer["ok"], answer["errors"]
    assert answer["source"].startswith("composed")
    assert "-- customer.active_buyer@1" in answer["sql"]
    assert answer["total"] == 449 and answer["reconciliation"]["status"] == "reconciles"


def test_filtered_answer_is_not_compared_with_the_headline(layer: Layer) -> None:
    spec = dict(GOLD, dimensions=["dimension.join_month"],
                filters=[{"dimension": "dimension.home_store:region", "op": "=", "value": "North"}])
    answer = ask.run(layer, spec)
    assert answer["ok"]
    assert answer["reconciliation"]["status"] == "not_comparable"


def test_undeclared_join_is_refused(layer: Layer) -> None:
    spec = {"entity": "customer.loyalty_member", "metric": "metric.customer_count",
            "dimensions": ["dimension.home_store:store"]}
    answer = ask.run(layer, spec)
    assert not answer["ok"]
    assert any("no declared relationship" in e for e in answer["errors"])


def test_snapshot_entity_without_period_is_refused(layer: Layer) -> None:
    answer = ask.run(layer, {"entity": "customer.active_buyer", "metric": "metric.customer_count", "dimensions": []})
    assert not answer["ok"] and any("give a period" in e for e in answer["errors"])


def test_lint_catches_a_dropped_certified_filter(layer: Layer) -> None:
    model = load_model(layer)
    spec = dict(GOLD, dimensions=["dimension.home_store:region"])
    sql, _, _ = ask.compose(model, spec, "cloud")
    tampered = sql.replace("e.is_staff = FALSE AND ", "")
    result = ask.lint(tampered, model, spec, "cloud", "bigquery")
    assert not result["ok"]
    assert any(f["rule"] == "certified_filter" and "is_staff" in f["message"] for f in result["findings"])


def test_lint_catches_an_undeclared_join(layer: Layer) -> None:
    model = load_model(layer)
    spec = dict(GOLD, dimensions=["dimension.home_store:region"])
    sql, _, _ = ask.compose(model, spec, "cloud")
    tampered = sql.replace("d1.store_id = e.home_store_id", "d1.store_id = e.customer_id")
    rules = {f["rule"] for f in ask.lint(tampered, model, spec, "cloud", "bigquery")["findings"]}
    assert "declared_joins" in rules


def test_uncertified_answer_carries_warnings_and_open_questions(layer: Layer) -> None:
    spec = {"entity": "customer.campaign_reachable", "metric": "metric.customer_count",
            "dimensions": ["dimension.home_store:region"], "period": "2026-08"}
    answer = ask.run(layer, spec)
    assert answer["ok"]
    assert any("reviewed, not certified" in w for w in answer["warnings"])
    assert sum("open question" in w for w in answer["warnings"]) == 2  # deduplicated across drafts


def test_legacy_platform_answer(layer: Layer) -> None:
    answer = ask.run(layer, dict(GOLD, dimensions=["dimension.home_store:region"]), platform="legacy")
    assert answer["ok"], answer["errors"]
    assert answer["platform"] == "legacy" and answer["total"] == 446
