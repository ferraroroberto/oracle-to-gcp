"""Anti-drift guard for this repo's `.fleet.toml` `[e2e]` table.

Loads the real `.fleet.toml` and asserts representative paths land in the
tier their rule intends — an edit that silently under-routes a real e2e
surface fails here. Mechanism + fail-safe behavior are covered by
project-scaffolding's own `tests/test_classify_e2e.py`; this file only
guards this repo's declared rules.
"""

from __future__ import annotations

from pathlib import Path

from scripts.classify_e2e import classify, load_config

REPO_ROOT = Path(__file__).resolve().parent.parent
REAL_FLEET_TOML = REPO_ROOT / ".fleet.toml"


def test_real_fleet_toml_declares_usable_e2e_table() -> None:
    cfg = load_config(REAL_FLEET_TOML)
    assert cfg.source == "declared", (
        "this repo's .fleet.toml must declare a usable [e2e] table"
    )
    assert cfg.rules, "at least one [[e2e.rule]] must be declared"


def test_real_rules_route_representative_paths() -> None:
    cfg = load_config(REAL_FLEET_TOML)

    def tier(*paths: str) -> str:
        return classify(list(paths), cfg).tier

    # The Streamlit app -> full. The e2e suite's shared conftest -> full too;
    # test_smoke.py itself is the only e2e module today and nothing else in
    # the suite imports it, so it narrows to "surface" (runs only itself).
    assert tier("app/app.py") == "full"
    assert tier("app/views/translator_demo.py") == "full"
    assert tier("tests/e2e/test_smoke.py") == "surface"
    assert tier("tests/e2e/conftest.py") == "full"

    # The standalone audits + the rest of the non-e2e test tree: nothing the
    # app imports from them -> skip.
    assert tier("tests/audits/schema_compatibility_audit.py") == "skip"
    assert tier("tests/test_bigquery_io.py") == "skip"

    # The self-contained semantic_layer/ spike (never imported by app/) and
    # docs/markdown -> skip.
    assert tier("semantic_layer/sl/cli.py") == "skip"
    assert tier("docs/architecture.mmd") == "skip"
    assert tier("README.md") == "skip"

    # Unmatched paths the app does read from (src/, config/pipeline.json,
    # config/examples/ demo data) fail safe to full rather than being guessed "none".
    assert tier("src/bigquery_io.py") == "full"
    assert tier("config/pipeline.json") == "full"
    assert tier("config/examples/demo_oracle_script.sql") == "full"

    # Mixed real diff (app + an inert doc) -> full.
    assert tier("app/app.py", "docs/architecture.mmd") == "full"


def test_ratchet_baseline_routes_to_skip() -> None:
    cfg = load_config(REAL_FLEET_TOML)
    assert classify([".static-ratchet-baseline.json"], cfg).tier == "skip"
    assert classify([".docs-ratchet-baseline.json"], cfg).tier == "skip"
    assert classify([".structure-ratchet-baseline.json"], cfg).tier == "skip"
    assert classify([".instruction-ratchet-baseline.json"], cfg).tier == "skip"
    assert classify([".doc-refs-ratchet-baseline.json"], cfg).tier == "skip"
    assert classify([".dead-code-ratchet-baseline.json"], cfg).tier == "skip"
    assert classify([".comment-ratchet-baseline.json"], cfg).tier == "skip"
    assert classify([".conventions-ratchet-baseline.json"], cfg).tier == "skip"
