"""`sl build` (deterministic, --check) and the find commands."""

from __future__ import annotations

import json
from pathlib import Path

from semantic_layer.sl import build
from semantic_layer.sl.common import Layer


def test_committed_build_is_up_to_date(layer: Layer) -> None:
    assert build.check(layer) == []


def test_build_check_detects_stale_output(layer: Layer, layer_root: Path) -> None:
    path = layer_root / "domains/core_analytics/metrics/metric.customer_count.yaml"
    path.write_text(path.read_text(encoding="utf-8").replace("Number of customers", "Customer total"), encoding="utf-8")
    stale = build.check(layer)
    assert "model.json" in stale and "docs/definitions/metric.customer_count.md" in stale


def test_index_and_model_are_consistent(layer: Layer) -> None:
    build.build(layer)
    lines = (layer.build / "index.jsonl").read_text(encoding="utf-8").splitlines()
    model = json.loads((layer.build / "model.json").read_text(encoding="utf-8"))
    assert len(lines) == len(model["definitions"]) == 20
    assert "rel.active_buyer__home_store" in model["definitions"]["customer.active_buyer"]["used_by"]
    store_targets = next(t for t in model["tables"] if t["table"] == "DWH.STORE_TARGETS")
    assert store_targets["migration_status"] == "pending" and store_targets["used_by"] == []


def test_find_commands(sl) -> None:
    code, out = sl("search", "store")
    assert code == 0 and "dimension.home_store" in out and "dimension.first_purchase_store" in out
    code, out = sl("show", "customer.active_buyer")
    assert code == 0 and "steward **avery.lane**" in out
    code, out = sl("table", "customer_monthly")
    assert "customer.campaign_reachable" in out and "migrated" in out
    code, out = sl("lineage", "dimension.home_store")
    assert "rel.campaign_reachable__home_store" in out
    code, out = sl("owners", "--team", "marketing")
    assert "sam.ortiz" in out
    code, out = sl("show", "customer.nope")
    assert code == 2 and "Unknown definition" in out
