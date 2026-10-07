# Project Instructions

Canonical instructions for AI coding agents in this repository. Claude Code reads this file directly as project memory; other agents (Cursor, Codex, etc.) reach it via the one-line `AGENTS.md` pointer.

## Streamlit conventions
*Apply only if this project uses Streamlit.*

- `st.set_page_config(layout="wide", page_title="...")` MUST be the first Streamlit call.
- Use `width="stretch"` (and `width="content"` where appropriate) in new and modified code. **Never** introduce new `use_container_width=True` (deprecated); migrate existing uses when you touch them.
- All mutable state in `st.session_state`. No module-level globals.
- `@st.cache_data` for DataFrames/files; `@st.cache_resource` for DB clients/models.
- Every widget needs a stable, explicit `key=`.
- UI code only in the UI directory (e.g. `app/`). Data logic stays in the non-UI package (e.g. `src/`). Never import `streamlit` from non-UI code.
- User feedback via `st.error()` / `st.warning()` / `st.success()`, not `st.write()`.
- **App layout:** the main file (`app.py`) handles only page config, shared state, the sidebar, and routing. Views live under `app/views/`, one file per page. Use `st.tabs()` for sub-sections *within* a view, and a sidebar radio only when asked.
- **Ask before assuming (Streamlit specifics):** `st.session_state` key names & scope; caching strategy (`@st.cache_data` TTL vs. `@st.cache_resource`); widget `key=` names & input sources; page placement (new page vs. a section in an existing page). (The universal "ask before assuming" directive is in global.)

## Verification (before declaring a task done)

One pre-ship gate runs byte-compile, `ruff`, the semantic layer gate (`validate`, `build --check`, `eval`), and `pytest` (unit + headless e2e boot smoke test + `semantic_layer/tests`):

```powershell
& .\scripts\verify-before-ship.ps1
```

Individual stages, if iterating on one:
- Syntax: `& .\.venv\Scripts\python.exe -m compileall -q app src tests`
- Lint: `ruff check .`
- Unit tests only: `& .\.venv\Scripts\python.exe -m pytest --ignore=tests/e2e`
- Full suite incl. e2e (needs `playwright install chromium` once): `& .\.venv\Scripts\python.exe -m pytest`

Run `verify-before-ship.ps1` before declaring any UI-touching change done — it auto-boots Streamlit for the e2e smoke test, so it never skips the boot check the way a bare `pytest --ignore=tests/e2e` would.

## Restart and verify before hand-off

No tray, PWA, or long-lived background service — `launch_app.bat` starts a local Streamlit dev server on demand (default `http://localhost:8501`). The file-watcher hot-reloads most edits; a full restart is only needed after changes to `st.set_page_config`, top-level imports, or `config/pipeline.json` defaults read at import time.

**Restart safely.** Close the console window `launch_app.bat` opened (or `Ctrl+C` in it) rather than killing Python processes by name (could take down an unrelated Python process). Re-run `launch_app.bat` (or `& .\.venv\Scripts\python.exe -m streamlit run app\app.py`) and confirm the browser reloads the **Execution** page with the new behavior visible before calling a change done.

## Internal architecture

[`docs/architecture.mmd`](docs/architecture.mmd) is a hand-authored Mermaid diagram of this repo's internal structure (Streamlit `app/` views, the `src/pipelines/oracle_to_bigquery.py` orchestrator and its mock pipeline stages, the local-LLM-hub dependency, the standalone `unit_test/` schema audit, `tests/`/`scripts/`) — the per-repo counterpart to the fleet diagram `ferraroroberto/fleet-config`'s `/system-map` generates. Update it in the same PR as any material structural change (new pipeline stage, module moved or renamed, new external dependency), like a `.fleet.toml` `description`. Not auto-generated, not covered by `scripts/verify-before-ship.ps1`.

## This repository
Oracle to GCP is a local Streamlit + Python pipeline prototype translating Oracle SQL scripts to BigQuery Standard SQL through a deterministic, inspectable validation loop. It uses SQLite mock databases for the Oracle and BigQuery sides, with the local LLM hub called only as a stateless translation function when available.

`semantic_layer/` is a separate, self-contained research spike (a mock team semantic layer + `sl` toolkit + `.github/skills/semantic-*` agent skills). It must never import from `src/` or `unit_test/` — it is designed to be lifted out and ported. After changing its YAML under `semantic_layer/domains/`, run `python -m semantic_layer build` and commit the regenerated `semantic_layer/build/` (the gate's `build --check` fails otherwise). Its content is fictitious; this repo is public.

See `README.md` for operation and `docs/architecture-rationale.md` for design reasoning.
