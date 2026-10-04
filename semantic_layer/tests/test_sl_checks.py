"""`sl checks run` (Phase 1) — drift, dates, and `unknown` is never a pass."""

from __future__ import annotations

from pathlib import Path

import pytest

from semantic_layer.sl import checks
from semantic_layer.sl.common import Layer
from semantic_layer.sl.platforms import PlatformUnavailable
from semantic_layer.tests.conftest import execute


def test_clean_layer_is_healthy(layer: Layer) -> None:
    report = checks.run(layer, today="2026-10-20")
    assert report["summary"] == {"ok": 20, "unknown": 0, "warning": 0, "failing": 0}
    assert report["catalog"]["status"] == "unchanged"
    assert (layer.build / "health.json").exists()


def test_renamed_column_is_reported_as_drift(layer: Layer, layer_root: Path) -> None:
    execute(layer_root, "cloud",
            "ALTER TABLE analytics.customer_monthly RENAME COLUMN last_purchase_date TO last_order_date")
    report = checks.run(layer, today="2026-10-20")
    findings = report["definitions"]["customer.active_buyer"]
    assert findings["status"] == "failing"
    assert any("last_purchase_date" in f["message"] and f["check"] == "schema_drift" for f in findings["findings"])
    assert report["catalog"]["status"] == "changed"
    issue = (layer.root / "build" / "issues" / "core_analytics.md").read_text(encoding="utf-8")
    assert "@avery.lane" in issue and "last_purchase_date" in issue


def test_overdue_review_is_a_warning(layer: Layer) -> None:
    report = checks.run(layer, today="2027-05-01")
    assert report["definitions"]["customer.active_buyer"]["status"] == "warning"
    assert report["definitions"]["tpl.active_customers_by_store_and_join_month"]["status"] == "failing"  # expired


def test_unreachable_platform_is_unknown_not_ok(layer: Layer, monkeypatch: pytest.MonkeyPatch,
                                                sl) -> None:
    def unavailable(*_args, **_kwargs):
        raise PlatformUnavailable("network down")

    monkeypatch.setattr(checks, "get_platform", unavailable)
    monkeypatch.setattr(checks.catalog_mod, "collect", unavailable)
    report = checks.run(layer, today="2026-10-20")
    assert report["summary"]["ok"] < 20
    assert report["definitions"]["customer.active_buyer"]["status"] == "unknown"
    assert report["catalog"]["status"] == "unknown"
    code, out = sl("checks", "run", "--today", "2026-10-20")
    assert code == 3 and "UNKNOWN, not passed" in out
