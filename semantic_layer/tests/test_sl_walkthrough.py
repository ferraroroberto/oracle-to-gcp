"""The README's 10-step pilot walkthrough, end to end through the CLI, with every Phase 2 flag off."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from semantic_layer.sl.common import PACKAGE_ROOT
from semantic_layer.tests.conftest import execute

GOLD = {"entity": "customer.active_buyer", "metric": "metric.customer_count",
        "dimensions": ["dimension.home_store:region", "dimension.home_store:store", "dimension.join_month"],
        "period": "2026-08"}


def test_pilot_walkthrough(layer_root: Path, sl, tmp_path: Path) -> None:
    config = json.loads((layer_root / "config.json").read_text(encoding="utf-8"))
    assert not any(config["phase2"].values())

    # 1–2. mock warehouses + catalog: regenerating changes nothing committed
    assert sl("mock", "seed")[0] == 0
    before = {p.name: p.read_text(encoding="utf-8") for p in (layer_root / "catalog").rglob("*.yaml")}
    assert sl("catalog", "generate")[0] == 0
    after = {p.name: p.read_text(encoding="utf-8") for p in (layer_root / "catalog").rglob("*.yaml")}
    assert before == after

    # 3. harvest team A's query: probes + interview questions
    code, out = sl("harvest", "analyse", str(layer_root / "queries/core_analytics_pilot.oracle.sql"),
                   "--platform", "legacy", "--session", "replay", "--json")
    questions = json.loads(out)["questions"]
    assert code == 0 and {q["topic"] for q in questions} >= {"purpose", "literal", "join", "anchor", "owners"}

    # 4–5. the drafts from the recorded interview are committed and certified → validate + build are clean
    assert sl("validate")[0] == 0
    assert sl("build", "--check")[0] == 0

    # 6. ambiguity first, then a template-backed, reconciled answer
    code, out = sl("ask", "resolve", "how many customers per store", "--json")
    assert json.loads(out)["needs_clarification"]
    spec = tmp_path / "spec.json"
    spec.write_text(json.dumps(GOLD), encoding="utf-8")
    code, out = sl("ask", "run", str(spec), "--json")
    answer = json.loads(out)
    assert code == 0 and answer["total"] == 446 and answer["reconciliation"]["status"] == "reconciles"

    # 7. capture a clarification as a draft
    fields = tmp_path / "fields.json"
    fields.write_text(json.dumps({"name": "Frequent buyer", "grain": "one row per customer per month-end snapshot",
                                  "bindings": [{"platform": "cloud", "source": "analytics.customer_monthly",
                                                "key": "customer_id", "time_column": "snapshot_date",
                                                "filter": "{t}.orders_12m >= 6"}]}), encoding="utf-8")
    code, out = sl("capture", "definition", "--kind", "entity", "--id", "customer.frequent_buyer",
                   "--team", "core_analytics", "--steward", "avery.lane",
                   "--note", "frequent = six or more orders a year", "--fields", str(fields))
    assert code == 0 and "It validates" in out

    # 8. the two teams disagree, with numbers
    code, out = sl("disagree")
    assert "446" in out and "398" in out

    # 9. a renamed column is caught by the scheduled checks
    execute(layer_root, "cloud", "ALTER TABLE analytics.stores RENAME COLUMN region TO sales_region")
    code, out = sl("checks", "run", "--today", "2026-10-20")
    assert code == 1 and "region" in out and "dimension.home_store" in out

    # 10. evals (on a fresh copy of the mock, since step 9 broke the stores table)
    execute(layer_root, "cloud", "ALTER TABLE analytics.stores RENAME COLUMN sales_region TO region")
    code, out = sl("eval")
    assert code == 0 and "evals pass" in out


def test_pilot_path_never_loads_phase2_code(layer_root: Path) -> None:
    script = (
        "import sys, json\n"
        "from semantic_layer.sl import cli\n"
        f"root = {str(layer_root)!r}\n"
        "for args in (['validate'], ['build', '--check'], ['ask', 'resolve', 'active customers'], ['disagree'],"
        " ['checks', 'run', '--today', '2026-10-20'], ['eval']):\n"
        "    cli.main(args + ['--root', root])\n"
        "loaded = [m for m in sys.modules if m.startswith('semantic_layer.sl.phase2.')]\n"
        "print('PHASE2_LOADED=' + json.dumps(loaded))\n"
    )
    result = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True, encoding="utf-8",
                            cwd=PACKAGE_ROOT.parent, check=False,
                            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    line = next(line for line in result.stdout.splitlines() if line.startswith("PHASE2_LOADED="))
    assert json.loads(line.split("=", 1)[1]) == [], result.stderr
