# Project Instructions

Canonical instructions for AI coding agents working in this repository. Claude Code reads this file directly as project memory. Other agents (Cursor, Codex, etc.) reach it via the one-line `AGENTS.md` pointer.

## Streamlit conventions
*Apply only if this project uses Streamlit.*

- `st.set_page_config(layout="wide", page_title="...")` MUST be the first Streamlit call.
- Use `width="stretch"` (and `width="content"` where appropriate) in new and modified code. **Never** introduce new `use_container_width=True` — it is deprecated. When you touch existing code that uses `use_container_width`, migrate it.
- All mutable state in `st.session_state`. No module-level globals.
- `@st.cache_data` for DataFrames/files; `@st.cache_resource` for DB clients/models.
- Every widget needs a stable, explicit `key=`.
- UI code only in the UI directory (e.g. `app/`). Data logic stays in the non-UI package (e.g. `src/`). Never import `streamlit` from non-UI code.
- User feedback via `st.error()` / `st.warning()` / `st.success()`, not `st.write()`.
- **App layout:** the main file (`app.py`) handles only page config, shared state, the sidebar, and routing. Views live under `app/views/`, one file per page. Use `st.tabs()` for sub-sections *within* a view, and a sidebar radio only when asked.
- **Ask before assuming (Streamlit specifics):** `st.session_state` key names & scope; caching strategy (`@st.cache_data` TTL vs. `@st.cache_resource`); widget `key=` names & input sources; page placement (new page vs. a section in an existing page). (The universal "ask before assuming" directive is in global.)

## Verification (before declaring a task done)

This repo ships a single pre-ship gate that runs byte-compile, `ruff`, and `pytest` (unit + the headless e2e boot smoke test) as one pass/fail pipeline:

```powershell
& .\scripts\verify-before-ship.ps1
```

Individual stages, if iterating on just one:
- Syntax: `& .\.venv\Scripts\python.exe -m compileall -q app src tests`
- Lint: `ruff check .`
- Unit tests only: `& .\.venv\Scripts\python.exe -m pytest --ignore=tests/e2e`
- Full suite incl. e2e (needs `playwright install chromium` once): `& .\.venv\Scripts\python.exe -m pytest`

Run `verify-before-ship.ps1` before any UI-touching change is declared done — it auto-boots Streamlit for the e2e smoke test, so it never silently skips the boot check the way a bare `pytest --ignore=tests/e2e` would.

## Restart and verify before hand-off

This repo ships no tray, no PWA, and no long-lived background service — `launch_app.bat` starts a local Streamlit dev server on demand (default `http://localhost:8501`), nothing more. Streamlit's file-watcher hot-reloads most edits automatically; a full restart is only needed after changes to `st.set_page_config`, top-level imports, or `config/pipeline.json` defaults read at import time.

**Restart safely.** Close the console window `launch_app.bat` opened (or `Ctrl+C` inside it) rather than killing Python processes by name — a name-based kill risks taking down an unrelated Python process on the same machine. Re-run `launch_app.bat` (or `& .\.venv\Scripts\python.exe -m streamlit run app\app.py`) and confirm the browser reloads the **Execution** page with the new behavior visible before calling a change done.

## This repository
Oracle to GCP is a local Streamlit + Python pipeline prototype for translating Oracle SQL scripts to BigQuery Standard SQL through a deterministic, inspectable validation loop. It currently uses SQLite mock databases for the Oracle and BigQuery sides, with the local LLM hub called only as a stateless translation function when available.

No tray or PWA process is shipped in this repo. For code changes, run `scripts/verify-before-ship.ps1`; for manual exploration, start Streamlit with `launch_app.bat` or `python -m streamlit run app/app.py`. See `README.md` for operation and `docs/architecture-rationale.md` for the design reasoning.
