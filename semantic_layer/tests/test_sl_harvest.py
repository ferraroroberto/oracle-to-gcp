"""`sl harvest` — parse facts, aggregate-only probes, and drafts that reproduce the committed layer."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import yaml

from semantic_layer.sl import harvest
from semantic_layer.sl.common import PACKAGE_ROOT, Layer

PILOT = "queries/core_analytics_pilot.oracle.sql"
MARKETING = "queries/marketing_pilot.bigquery.sql"


def test_parse_extracts_structure_from_the_pilot_query(layer: Layer) -> None:
    facts = harvest.parse_sql((layer.root / PILOT).read_text(encoding="utf-8"), "oracle")
    assert [c["name"] for c in facts["ctes"]] == ["active"]
    assert {t["table"] for t in facts["tables"]} == {"DWH.CUSTOMER_MONTHLY", "DWH.STORES", "DWH.CUSTOMERS"}
    literals = {f["predicate"]: f["literals"] for f in facts["filters"]}
    assert literals["cm.ACCOUNT_STATUS = 'A'"] == ["A"]
    assert literals["cm.IS_STAFF = 0"] == ["0"]
    stores_join = next(j for j in facts["joins"] if j["right_alias"] == "s")
    assert stores_join["keys"][0]["left_source"] == ["DWH.CUSTOMER_MONTHLY.HOME_STORE_ID"]  # resolved through the CTE
    join_month = next(o for o in facts["outputs"] if o["name"] == "JOIN_MONTH")
    assert join_month["sources"] == ["DWH.CUSTOMERS.FIRST_PURCHASE_DATE"]


def test_parse_flags_a_joined_but_unused_table(layer: Layer) -> None:
    facts = harvest.parse_sql((layer.root / MARKETING).read_text(encoding="utf-8"), "bigquery")
    assert facts["unused_joins"] == [{"alias": "lm", "table": "analytics.loyalty_members", "type": "left"}]


def test_probes_are_aggregate_only(layer: Layer) -> None:
    facts = harvest.parse_sql((layer.root / PILOT).read_text(encoding="utf-8"), "oracle")
    probes = harvest.probe(facts, layer, "legacy")
    # Code columns only — never the date period, never a key column's values.
    assert {(d["table"], d["column"]) for d in probes["distributions"]} == {
        ("DWH.CUSTOMER_MONTHLY", "ACCOUNT_STATUS"), ("DWH.CUSTOMER_MONTHLY", "IS_STAFF")}
    for dist in probes["distributions"]:
        assert len(dist["values"]) <= harvest.MAX_DISTRIBUTION_VALUES
        assert set(dist["values"][0]) == {"value", "share"}
    for key in probes["keys"]:
        assert set(key) == {"table", "column", "rows", "distinct", "unique"}  # counts, not ids
    assert {j["cardinality"] for j in probes["join_cardinality"]} == {"many_to_one"}


def test_cross_reference_spots_the_marketing_conflict(layer: Layer, layer_root: Path) -> None:
    # State before marketing's own harvest: only core-analytics definitions exist on that table.
    shutil.rmtree(layer_root / "domains" / "marketing")
    facts = harvest.parse_sql((layer.root / MARKETING).read_text(encoding="utf-8"), "bigquery")
    xref = harvest.cross_reference(facts, layer, "cloud")
    conflict = next(c for c in xref["filter_comparisons"] if c["definition"] == "customer.active_buyer")
    assert conflict["outcome"] == "differs"
    assert "account_status = 'A'" in conflict["only_in_definition"]
    questions = harvest.questions(facts, xref, None)
    assert questions[1]["topic"] == "conflict"  # asked right after the purpose question
    assert any(q["topic"] == "unused" for q in questions)


def test_marketing_query_matches_its_harvested_definition(layer: Layer) -> None:
    facts = harvest.parse_sql((layer.root / MARKETING).read_text(encoding="utf-8"), "bigquery")
    xref = harvest.cross_reference(facts, layer, "cloud")
    assert [(c["definition"], c["outcome"]) for c in xref["filter_comparisons"]] == [
        ("customer.campaign_reachable", "matches")]


def test_pilot_query_matches_its_own_certified_definition(layer: Layer) -> None:
    facts = harvest.parse_sql((layer.root / PILOT).read_text(encoding="utf-8"), "oracle")
    xref = harvest.cross_reference(facts, layer, "legacy")
    assert [c["outcome"] for c in xref["filter_comparisons"]] == ["matches"]


def test_draft_reproduces_the_committed_harvest(layer: Layer, layer_root: Path) -> None:
    """Re-running analyse + draft from the recorded answers yields the committed ids and legacy bindings."""
    committed = {p.stem: yaml.safe_load(p.read_text(encoding="utf-8"))
                 for p in (PACKAGE_ROOT / "domains").rglob("*.yaml")}
    harvested = {k for k, v in committed.items() if (v.get("provenance") or {}).get("method") == "harvest"}
    for path in (layer_root / "domains").rglob("*.yaml"):
        if path.stem in harvested:
            path.unlink()
    for path in (layer_root / "domains").rglob("*.sql"):
        path.unlink()
    for session, query, platform in (("2026-10-03-core-analytics", PILOT, "legacy"),
                                     ("2026-10-10-marketing", MARKETING, "cloud")):
        answers = layer_root / "harvest" / "sessions" / session / "answers.yaml"
        keep = answers.read_text(encoding="utf-8")
        shutil.rmtree(answers.parent)
        harvest.analyse(layer, layer_root / query, platform, session)
        answers.write_text(keep, encoding="utf-8")
        harvest.draft(layer, session)

    drafted = {p.stem: yaml.safe_load(p.read_text(encoding="utf-8"))
               for p in (layer_root / "domains").rglob("*.yaml")}
    assert harvested <= set(drafted)
    for def_id in harvested:
        assert drafted[def_id]["status"] == "draft"  # drafts never self-certify
        for binding in drafted[def_id].get("bindings", []):
            original = next(b for b in committed[def_id]["bindings"] if b["platform"] == binding["platform"])
            assert binding["source"] == original["source"]
            assert binding["key"] == original["key"]
    active = next(b for b in drafted["customer.active_buyer"]["bindings"] if b["platform"] == "legacy")
    assert active["filter"] == next(b for b in committed["customer.active_buyer"]["bindings"]
                                    if b["platform"] == "legacy")["filter"]
    proposed = next(b for b in drafted["customer.active_buyer"]["bindings"] if b["platform"] == "cloud")
    assert proposed["status"] == "proposed" and "confirm" in proposed["review_note"]
    rel = drafted["rel.active_buyer__home_store"]
    assert rel["join"] == committed["rel.active_buyer__home_store"]["join"]
    assert rel["cardinality"] == "many_to_one"


def test_draft_refuses_to_overwrite_reviewed_work(layer: Layer) -> None:
    session = "2026-10-03-core-analytics"
    try:
        harvest.draft(layer, session)
    except Exception as exc:  # noqa: BLE001
        assert "exists" in str(exc)
    else:
        raise AssertionError("draft overwrote a committed definition")


def test_session_artifacts_are_json(layer: Layer) -> None:
    folder = layer.root / "harvest" / "sessions" / "2026-10-03-core-analytics"
    for name in ("parse.json", "xref.json", "probes.json", "questions.json"):
        json.loads((folder / name).read_text(encoding="utf-8"))
