# Layout

Where code goes. The root layout is checked against the `streamlit` shape declared in `.fleet.toml`; the structure ratchet (`scripts/structure_ratchet.py`, run by the gate) fails on a file over 1,000 lines, a function over 100 lines, a directory past 40 modules, a `src` module importing `app`, or a root folder outside the shape plus the exceptions below. Existing offenders are recorded in `.structure-ratchet-baseline.json` at their current size and may not grow.

## Top-level folders

- `app/` — Streamlit UI only. `app.py` handles page config, shared state, the sidebar and routing; `views/` holds one file per page (`welcome.py`, `translator_demo.py`, `process_map.py`); `styles/` holds the stylesheet. `log_panel.py` is the shared log widget.
- `src/` — logic layer, never imports `streamlit` or `app`. Flat modules for config, connections, execution, mock environment, SQL processing, translation, validation, preflight, lineage and the GCS/BigQuery IO helpers; `pipelines/` holds the end-to-end orchestrator (`oracle_to_bigquery.py`).
- `tests/` — unit tests for `src/` and `unit_test/`; `e2e/` boots Streamlit headlessly for the smoke test.
- `scripts/` — the pre-ship gate (`verify-before-ship.ps1`) and the vendored ratchets with their wrappers.
- `data/` — runtime input, output and log folders; contents are not tracked.
- `docs/` — durable reference: architecture rationale and diagram, design records, this file.

## Declared exceptions (`extra_roots`)

These roots sit outside the `streamlit` preset on purpose:

- `config/` — `pipeline.json`, the pipeline defaults read at import time. Configuration, not code, so it has no home in `src/`.
- `examples/` — the fictitious demo Oracle script and the source-to-target mapping registry the UI and CLI load as sample input.
- `semantic_layer/` — a self-contained research spike with its own package (`sl/`), definitions (`domains/`, `schema/`, `catalog/`, `harvest/`, `queries/`, `evals/`), generated `build/` and its own `tests/`. It never imports from `src/` or `unit_test/` so it can be lifted out whole; that independence is why it is a second root and not a subpackage of `src/`.
- `unit_test/` — standalone audit experiments (schema compatibility, query cost, query optimization loop) with their config templates. They are runnable tools, not test suites; their tests live in `tests/`.
