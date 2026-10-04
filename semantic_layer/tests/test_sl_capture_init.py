"""`sl capture` (layer grows from use) and `sl init --clean` (start from scratch)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from semantic_layer.sl import capture, init
from semantic_layer.sl.common import Layer, LayerError
from semantic_layer.sl.validate import validate

FIELDS = {"name": "High-value customer", "grain": "one row per customer per month-end snapshot",
          "description": "Active customer with at least 10 orders in the last 12 months.",
          "bindings": [{"platform": "cloud", "source": "analytics.customer_monthly", "key": "customer_id",
                        "time_column": "snapshot_date", "filter": "{t}.orders_12m >= 10"}]}


def test_captured_definition_is_a_valid_draft(layer: Layer) -> None:
    path = capture.definition(layer, "entity", "customer.high_value", "core_analytics", "avery.lane",
                              "user: 'high value means 10+ orders a year'", FIELDS)
    text = path.read_text(encoding="utf-8")
    assert "status: draft" in text and "method: capture" in text
    assert validate(layer).ok


def test_capture_needs_a_binding(layer: Layer) -> None:
    with pytest.raises(LayerError, match="needs bindings"):
        capture.definition(layer, "entity", "customer.vague", "core_analytics", "avery.lane", "note",
                           {"grain": "one row"})


def test_capture_refuses_an_existing_id(layer: Layer) -> None:
    with pytest.raises(LayerError, match="already exists"):
        capture.definition(layer, "entity", "customer.active_buyer", "core_analytics", "avery.lane", "n", FIELDS)


def test_capture_issue_targets_the_steward(sl) -> None:
    code, out = sl("capture", "issue", "--about", "customer.campaign_reachable", "--note", "too high", "--json")
    issue = json.loads(out)["issue"]
    assert code == 0 and issue["steward"] == "sam.ortiz" and "--assignee sam.ortiz" in issue["command"]


def test_init_clean_leaves_a_valid_empty_layer(layer: Layer, layer_root: Path, sl) -> None:
    code, out = sl("init", "--clean")
    assert code == 2 and "--yes" in out  # refuses without confirmation
    removed = init.clean(layer)
    assert removed > 50
    assert not list((layer_root / "domains").rglob("*.yaml"))
    assert (layer_root / "domains" / ".gitkeep").exists()
    assert validate(layer).ok
    code, _ = sl("catalog", "generate")
    assert code == 0 and validate(layer).ok
