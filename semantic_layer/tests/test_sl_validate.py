"""`sl validate` — the CI gate rejects every class of broken definition."""

from __future__ import annotations

from pathlib import Path

import yaml

from semantic_layer.sl.common import Layer
from semantic_layer.sl.validate import validate

ACTIVE = "domains/core_analytics/entities/customer.active_buyer.yaml"


def _edit(root: Path, rel: str, change) -> None:
    path = root / rel
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    change(data)
    path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")


def test_committed_layer_is_valid(layer: Layer) -> None:
    report = validate(layer)
    assert report.ok, report.errors
    assert not report.stale_templates


def test_schema_violation_is_an_error(layer_root: Path, layer: Layer) -> None:
    _edit(layer_root, ACTIVE, lambda d: d.update(status="approved"))
    assert any("schema" in e and "approved" in e for e in validate(layer).errors)


def test_dangling_reference_is_an_error(layer_root: Path, layer: Layer) -> None:
    _edit(layer_root, "domains/core_analytics/metrics/metric.customer_count.yaml",
          lambda d: d["applies_to"].append("customer.does_not_exist"))
    assert any("dangling reference 'customer.does_not_exist'" in e for e in validate(layer).errors)


def test_binding_column_missing_from_catalog_is_an_error(layer_root: Path, layer: Layer) -> None:
    def break_filter(d):
        d["bindings"][1]["filter"] = "{t}.no_such_column = 'A'"
    _edit(layer_root, ACTIVE, break_filter)
    assert any("column 'no_such_column' not found in cloud:analytics.customer_monthly" in e
               for e in validate(layer).errors)


def test_certified_entity_without_checks_is_an_error(layer_root: Path, layer: Layer) -> None:
    _edit(layer_root, ACTIVE, lambda d: d.pop("checks"))
    assert any("certified entity has no quality checks" in e for e in validate(layer).errors)


def test_shared_synonym_without_link_is_an_error(layer_root: Path, layer: Layer) -> None:
    # Remove both directions of the home store / first-purchase store link: "store" now collides silently.
    for rel in ("domains/core_analytics/dimensions/dimension.home_store.yaml",
                "domains/core_analytics/dimensions/dimension.first_purchase_store.yaml"):
        _edit(layer_root, rel, lambda d: d.pop("not_to_be_confused_with"))
    errors = validate(layer).errors
    assert any("synonym 'store'" in e and "dimension.first_purchase_store" in e for e in errors)


def test_version_bump_marks_templates_stale(layer_root: Path, layer: Layer) -> None:
    _edit(layer_root, ACTIVE, lambda d: d.update(version=2))
    report = validate(layer)
    assert report.ok  # a warning, not an error
    assert report.stale_templates == [{"template": "tpl.active_customers_by_store_and_join_month",
                                       "pinned": "customer.active_buyer@1", "current": "customer.active_buyer@2"}]
