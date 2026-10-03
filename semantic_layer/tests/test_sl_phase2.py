"""Phase 2 modules: gated off by default, and each one catches what it is for."""

from __future__ import annotations

import json
import re
from pathlib import Path

import yaml

from semantic_layer.sl import checks, disagree
from semantic_layer.sl.common import Layer, load_layer
from semantic_layer.sl.phase2 import bulk, export_osi, navigator
from semantic_layer.tests.conftest import enable_phase2, execute


def _edit(path: Path, change) -> None:
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    change(data)
    path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")


def test_phase2_commands_are_off_by_default(sl) -> None:
    for args in (("harvest", "bulk", "--history"), ("disagree", "--all"), ("checks", "run", "--full"),
                 ("build", "--navigator"), ("export", "osi")):
        code, out = sl(*args)
        assert code == 2 and "Phase 2 module" in out and "disabled" in out, args


def test_bulk_ranks_usage_and_finds_undeclared_joins(layer: Layer) -> None:
    report = bulk.run(layer, history=True)
    assert report["parsed"] == report["queries"] == 50
    ranked = [t["table"] for t in report["tables"]]
    assert ranked[:2] == ["analytics.stores", "analytics.customer_monthly"]
    undeclared = {c["join"] for c in report["join_candidates"] if not c["declared"]}
    assert "analytics.orders.store_id = analytics.stores.store_id" in undeclared
    assert "dwh.store_targets" in report["unused_catalog_tables"]
    assert (layer.root / "harvest" / "bulk" / "usage.md").exists()


def test_full_disagreement_compares_at_one_period_and_skips_parameters(layer: Layer) -> None:
    bulk.run(layer, history=True)
    report = disagree.run(layer, all_queries=True)
    tables = {d["table"]: d for d in report["disagreements"]}
    assert "analytics.stores" not in tables  # region = 'North' vs 'South' is a parameter, not a disagreement
    monthly = {v["team"]: v["count"] for v in tables["analytics.customer_monthly"]["variants"]}
    assert monthly["core_analytics"] == 449  # all variants evaluated at the latest period used (2026-09)
    assert len(monthly) == 3
    assert "analytics.orders" in tables  # finance excludes cancelled orders, store ops does not


def test_full_checks_clean(layer: Layer) -> None:
    report = checks.run(layer, today="2026-10-20", full=True)
    assert report["summary"]["failing"] == 0 and report["summary"]["unknown"] == 0


def test_full_checks_catch_a_stale_anchor(layer: Layer, layer_root: Path) -> None:
    _edit(layer_root / "domains/core_analytics/anchors/anchor.monthly_pack.active_customers.yaml",
          lambda d: d["values"][0].update(value=500))
    report = checks.run(layer, today="2026-10-20", full=True)
    assert report["definitions"]["anchor.monthly_pack.active_customers"]["status"] == "failing"
    template = report["definitions"]["tpl.active_customers_by_store_and_join_month"]
    assert any("needs_reverification" in f["message"] and "500" in f["message"] for f in template["findings"])


def test_full_checks_demote_a_template_after_a_definition_bump(layer: Layer, layer_root: Path) -> None:
    _edit(layer_root / "domains/core_analytics/entities/customer.active_buyer.yaml", lambda d: d.update(version=2))
    report = checks.run(layer, today="2026-10-20", full=True)
    findings = report["definitions"]["tpl.active_customers_by_store_and_join_month"]["findings"]
    assert {f["check"] for f in findings} >= {"stale_pin", "template_rerun"}


def test_full_checks_catch_legacy_cloud_divergence(layer: Layer, layer_root: Path) -> None:
    execute(layer_root, "legacy",
            "DELETE FROM DWH.CUSTOMER_MONTHLY WHERE SNAPSHOT_DATE = DATE '2026-09-30' AND CUSTOMER_ID % 10 = 0")
    report = checks.run(layer, today="2026-10-20", full=True)
    findings = report["definitions"]["customer.active_buyer"]["findings"]
    assert any(f["check"] == "reconcile" and "migration divergence" in f["message"] for f in findings)


def test_full_checks_catch_quality_problems(layer: Layer, layer_root: Path) -> None:
    execute(layer_root, "cloud", "UPDATE analytics.customer_monthly SET account_status = 'X' WHERE customer_id = 7")
    execute(layer_root, "cloud", "INSERT INTO analytics.loyalty_members VALUES (NULL, DATE '2026-01-01', 'GOLD')")
    report = checks.run(layer, today="2026-10-20", full=True)
    active = {f["check"] for f in report["definitions"]["customer.active_buyer"]["findings"]}
    loyalty = {f["check"] for f in report["definitions"]["customer.loyalty_member"]["findings"]}
    assert "invalidValues" in active and "nullValues" in loyalty
    stale = checks.run(layer, today="2027-01-15", full=True)  # data stops at 2026-09-30
    assert any(f["check"] == "freshness" for f in stale["definitions"]["customer.active_buyer"]["findings"])


def test_navigator_is_self_contained(layer: Layer) -> None:
    checks.run(layer, today="2026-10-20")
    html = navigator.write(layer).read_text(encoding="utf-8")
    assert not re.search(r"""(src|href)=["']?https?://""", html)
    blob = html.split('<script id="data" type="application/json">', 1)[1].split("</script>", 1)[0]
    data = json.loads(blob)
    assert len(data["model"]["definitions"]) == 20 and data["health"]["summary"]["ok"] == 20


def test_osi_export_structure(layer: Layer) -> None:
    doc = export_osi.export(layer, "cloud")
    assert doc["version"] == "0.2.0.dev0"
    datasets = {d["name"]: d for d in doc["datasets"]}
    assert {"customer_monthly", "stores", "customers"} <= set(datasets)
    for dataset in doc["datasets"]:
        for field in dataset["fields"]:
            assert field["expression"]["dialects"][0]["dialect"] == "BIGQUERY"
    metric = next(m for m in doc["metrics"] if m["name"] == "customer_count__active_buyer")
    assert "CASE WHEN" in metric["expression"]["dialects"][0]["expression"]
    rel = next(r for r in doc["relationships"] if r["name"] == "active_buyer__home_store")
    assert rel["from_columns"] == ["home_store_id"] and rel["to_columns"] == ["store_id"]
    json.loads(rel["custom_extensions"][0]["data"])  # vendor data is a JSON string per the spec


def test_enabled_flags_allow_the_commands(layer_root: Path, sl) -> None:
    enable_phase2(layer_root)
    assert load_layer(layer_root).phase2_enabled("navigator")
    code, _ = sl("build", "--navigator")
    assert code == 0 and (layer_root / "build" / "navigator.html").exists()
