# Oracle to GCP

Local prototype for translating Oracle SQL scripts into BigQuery Standard SQL through a deterministic, inspectable pipeline.

The current project is a working mock: it uses SQLite files as stand-ins for Oracle and BigQuery, ships a fictitious Oracle script, seeds both mock engines with sample data, runs translation and validation end to end, and emits a final BigQuery script plus a JSON run report. The local LLM hub is consulted as a stateless translation function when available; a deterministic fallback keeps the demo runnable offline.

## What It Demonstrates

- PL/SQL variable materialization for a simple `SELECT ... INTO` case.
- Ordered SQL unit splitting after the script has been reduced to pure SQL.
- Source-to-target table mapping through a registry.
- Input row-count parity checks for external source tables.
- Column-level schema compatibility checks for mapped Oracle↔BigQuery tables.
- Oracle-to-BigQuery translation with bounded repair attempts.
- Dual execution against mock Oracle and mock BigQuery SQLite databases.
- Fingerprint validation using row counts, numeric sums, and grouped aggregates.
- Inspectable Streamlit UI with live logs, unit status, final SQL, and report artifacts.

## Run

Create the local environment, install dependencies, then launch Streamlit:

```powershell
cd E:\automation\oracle-to-gcp
python -m venv .venv
& .\.venv\Scripts\python.exe -m pip install -r requirements.txt
& .\.venv\Scripts\python.exe -m streamlit run app\app.py
```

Or use:

```powershell
.\launch_app.bat
```

Open the **Execution** page. The page has five tabs:

- **Demo** — runs the built-in mock script.
- **Execution** — runs one `.sql` file or every `.sql` file in a directory.
- **Connection Configuration** — edits structured LLM, Google Cloud/BigQuery, and Oracle connection metadata and runs mock-safe connection tests.
- **Table Correspondence** — maintains the durable Oracle↔BigQuery registry, including CSV template download, CSV import, CSV export, and one-row manual edits.
- **Advanced JSON** — edits and saves the raw `config/pipeline.json` for advanced changes.

The sidebar also includes **Process Map**, an executive one-page visual of the migration workflow, technology stack, tradeoffs, and value proposition. The standalone HTML map and its snapshot live next to the docs at `docs/oracle-to-gcp-process-map.html` and `docs/oracle-to-gcp-process-map.png`.

### Share the demo

`launch_server.bat` (Windows) / `launch_server.sh` (macOS/Linux) start the same Streamlit app but also expose it publicly over a [Cloudflare Tunnel](https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/downloads/) HTTPS URL, so the demo can be shared with anyone without deploying it anywhere:

```powershell
.\launch_server.bat
```

```bash
./launch_server.sh
```

Requires `cloudflared` on `PATH` (`winget install Cloudflare.cloudflared` on Windows, `brew install cloudflared` / `apt install cloudflared` elsewhere). Both scripts start Streamlit on port `8501` and print the public `https://` tunnel URL to share; `Ctrl+C` stops the tunnel (and the underlying Streamlit process).

## Mock Inputs

- Oracle demo script: `examples/demo_oracle_script.sql`
- Legacy seed mapping registry: `examples/mapping_registry.json`
- Durable correspondence registry: `data/table_registry.db`
- Runtime copies: `data/input/`
- Generated artifacts: `data/output/mock_run/`

The demo script declares `v_run_date`, resolves it from `sales_orders`, creates an intermediate `daily_revenue` table, then aggregates revenue by segment. The mock BigQuery side uses mapped tables named `raw_sales_orders` and `raw_customer_segments`, plus a scratch table named `scratch_daily_revenue`.

## Local LLM Hub

Pipeline defaults live in:

```text
config/pipeline.json
```

That file controls the local hub endpoint, model, timeout, temperature, prompt messages, extra model parameters, Google Cloud/BigQuery metadata, Oracle metadata, repair settings, output directory, file-backed execution defaults, the table registry path, and trace/debug capture. Streamlit reads the same JSON file and shows the active prompt plus model parameters before a run.

Secrets do not belong in `config/pipeline.json`. Store only environment variable names there, then put real values in `.env` or the process environment. Common references are `LLM_API_KEY`, `GOOGLE_APPLICATION_CREDENTIALS`, `GOOGLE_OAUTH_CLIENT_SECRET`, `ORACLE_USERNAME`, and `ORACLE_PASSWORD`. Connection test results and UI JSON dumps redact sensitive keys.

File-backed execution defaults:

```json
{
  "execution": {
    "default_input_dir": "data/input/sql_scripts",
    "result_suffix": "_bq",
    "table_registry_path": "data/table_registry.db"
  }
}
```

By default the app attempts an OpenAI-shape request to:

```text
http://127.0.0.1:8000/v1/chat/completions
```

Default model:

```text
claude-haiku-4-5
```

Override with environment variables:

```powershell
$env:LLM_BASE_URL = "http://127.0.0.1:8000"
$env:LLM_MODEL = "claude-haiku-4-5"
$env:LLM_TIMEOUT_SECONDS = "8"
```

If the hub is unavailable or returns a candidate that fails deterministic guardrails, the demo falls back to the local translator. This is intentional for the mock: the product shape is the deterministic pipeline, not reliance on an opaque runtime agent.

## CLI Pipeline

Run the complete mock without the UI:

```powershell
& .\.venv\Scripts\python.exe -m src.pipelines.oracle_to_bigquery
```

Use another config file or one-run overrides:

```powershell
& .\.venv\Scripts\python.exe -m src.pipelines.oracle_to_bigquery `
  --config config\pipeline.json `
  --no-use-local-hub `
  --repair-limit 2 `
  --trace `
  --trace-max-query-rows 100 `
  --llm-model claude-haiku-4-5 `
  --llm-temperature 0 `
  --llm-extra-json '{"max_tokens": 1200}'
```

Run one SQL file:

```powershell
& .\.venv\Scripts\python.exe -m src.pipelines.oracle_to_bigquery `
  --input-sql E:\path\to\script.sql `
  --no-use-local-hub `
  --trace
```

Run every `.sql` file in a directory, non-recursively:

```powershell
& .\.venv\Scripts\python.exe -m src.pipelines.oracle_to_bigquery `
  --batch `
  --input-dir E:\path\to\sql-folder `
  --no-use-local-hub `
  --trace
```

Expected artifacts:

- `data/output/mock_run/final_bigquery.sql`
- `data/output/mock_run/run_report_<timestamp>.json`
- `data/output/mock_run/run_trace_<timestamp>.json`
- `data/output/mock_run/lineage.md`
- `data/output/mock_run/oracle_mock.db`
- `data/output/mock_run/bigquery_mock.db`

The trace JSON is the browsable audit trail for a run. In trace/debug mode it records pipeline stages, materialized variables, ordered SQL units, table preflight go/no-go results, mappings, row-count checks, every translation attempt, LLM request/response payloads, model parameters, query result samples, validation fingerprints, repair iterations, errors, and artifact paths.

`lineage.md` is a dependency-staged markdown map of the run's `sources`/`targets`: tables are grouped by stage (stage 0 = roots never produced by this run, each later stage = 1 + the deepest stage of the sources that feed it), so independent single-source chains render as distinct links instead of collapsing into one merged block. It is rendered inline in the Streamlit Execution tab under "SQL Lineage Map" for both fresh and previously loaded runs.

Before translation starts, the pipeline runs a table-readiness and schema preflight. It extracts external source tables from the SQL, seeds known demo mappings into the durable registry, inserts unknown source tables as `pending`, probes mapped Oracle and BigQuery mock tables with a cheap `LIMIT 1` read, records reachability timestamps, refreshes column-level compatibility rows in `column_mappings`, and stops before translation if any required table is unmapped, unreachable, or schema-incompatible. The **Table Correspondence** tab is the user-friendly maintenance surface: download the CSV template, fill or edit it in a spreadsheet, import it back, export the current SQLite registry for review, and inspect column presence/type mismatches from the latest schema preflight.

For file-backed execution, each input `script.sql` writes a sibling result directory named `script_bq` by default. That folder contains the final BigQuery SQL, report JSON, trace JSON, lineage markdown, mock database artifacts, a copy of the source SQL, and a text log. The Streamlit Execution tab can load previous results from those result folders after the app is reopened.

## Standalone Schema Audit

The repository also includes an experimental, config-driven schema audit module for the production-shaped step between table correspondence and SQL translation. It reads CSV, JSON, or Excel table pairs with `oracle_schema`, `oracle_table`, `bigquery_project`, `bigquery_dataset`, and `bigquery_table`, fetches authoritative column metadata from Oracle and BigQuery, compares column presence/type families/shape, and writes machine-readable reports for downstream translation.

The default config template lives at:

```text
unit_test/schema_audit_config.json
```

Run it with:

```powershell
& .\.venv\Scripts\python.exe -m unit_test.schema_compatibility_audit --config unit_test\schema_audit_config.json
```

By default the production adapters expect Oracle credentials in `ORACLE_USERNAME`, `ORACLE_PASSWORD`, and `ORACLE_DSN`, plus Google Application Default Credentials or `GOOGLE_APPLICATION_CREDENTIALS` for BigQuery. Excel input uses `pandas` and `openpyxl`. The module also ships SQLite adapters for local experiments and tests. Metadata is the source of truth; optional row sampling is recorded only as evidence in the output reports and does not drive compatibility decisions.

Expected outputs are CSV and JSON files under `data/output/schema_audit/` unless overridden in the config:

- `column_report.csv` / `column_report.json` — one row per compared column.
- `table_summary.csv` / `table_summary.json` — one row per table pair.

This does not replace the mock pipeline preflight yet. The existing `src.preflight` path still blocks mock translation runs and persists compatibility rows into the SQLite table registry. The standalone audit is for proving the production metadata approach before wiring it into the main pipeline.

## Standalone Query Cost Audit

A second experimental, config-driven module runs *after* a run's translated statements have already validated — it is a post-migration optimization layer, not part of the translate-and-validate loop. It reads a run report's `units[].bq_sql` (`src/sql_models.py`), estimates the BigQuery execution cost of each statement, ranks the most expensive ones, writes a markdown report, and appends an LLM-generated optimization-suggestions section (partitioning, clustering, rewrite ideas) using the same local hub client as translation (`src/llm_client.py`).

The default config template lives at:

```text
unit_test/query_cost_audit_config.json
```

Run it against a completed run's report with:

```powershell
& .\.venv\Scripts\python.exe -m unit_test.query_cost_audit --config unit_test\query_cost_audit_config.json
```

Two estimator modes are supported in `estimator.mode`:

- `bigquery` — a real dry-run query job (`total_bytes_processed`, no data scanned or returned) against Google Application Default Credentials or `GOOGLE_APPLICATION_CREDENTIALS`, converted to USD via `pricing.price_per_tib_usd` (on-demand pricing; default `6.25`).
- `mock` — a deterministic byte estimate proportional to SQL text length, for local experimentation without live BigQuery credentials. This has no relation to actual scan volume.

Set `llm.enabled` to `false` to skip the optimization-suggestions pass (e.g. when the local hub isn't running). The report is written to `data/output/query_cost_audit/cost_report.md` unless overridden in `output.report_md`.

## Standalone Query Optimization Loop

A third experimental module closes the loop the query cost audit deliberately leaves open: instead of a one-shot suggestions report, it iterates. For each query it asks the local hub for a cost-optimized rewrite, executes the candidate, and validates that it still produces the *same result* as the current accepted query using the same fingerprint comparison the mock pipeline uses (`src/validation.py::compare_fingerprints` — row count, per-column numeric sums, grouped count/sum). A candidate is only adopted if it validates **and** is strictly cheaper than the current baseline; a failed validation rolls back to the last accepted query and stops the loop immediately, never silently replacing the baseline.

The loop stops on any of three conditions:

- **Validation failure** — a candidate's result set diverges from the current baseline. The candidate is rejected and the loop stops.
- **Diminishing returns** — marginal cost improvement stays below `optimization.min_improvement_pct` for `optimization.diminishing_returns_streak` consecutive iterations.
- **Max iterations** — `optimization.max_iterations` is reached.

The full per-iteration history (candidate SQL, estimated cost, accept/reject decision, validation outcome) is kept, not just the final query — it is written to the markdown report alongside the baseline-vs-final cost improvement.

The default config template lives at:

```text
unit_test/query_optimization_loop_config.json
```

Run it against a completed run's report with:

```powershell
& .\.venv\Scripts\python.exe -m unit_test.query_optimization_loop --config unit_test\query_optimization_loop_config.json
```

It reuses the query cost audit's `estimator.mode` (`mock` / `bigquery`) and adds an `executor.mode`, the source of each candidate's materialized result rows for the correctness gate:

- `bigquery` — issues the real query against Google Application Default Credentials or `GOOGLE_APPLICATION_CREDENTIALS`.
- `sqlite` — runs SQL directly against a local SQLite database at `executor.db_path`, for local experimentation and tests without live BigQuery credentials.

Set `llm.enabled` to `false` to leave every query at its baseline (zero iterations attempted). The report is written to `data/output/query_cost_audit/optimization_report.md` unless overridden in `output.report_md`.

## Shared Fleet-Wide GCP IO Helpers

`src/gcs_io.py` and `src/bigquery_io.py` are this repo's canonical, real (non-mock) Google Cloud Storage and BigQuery IO helpers — the shared implementation other fleet repos with duplicate ad hoc GCS/BigQuery code should consume, per the consolidation tracked in issue #17. Both lazily import `google-cloud-storage` / `google-cloud-bigquery` inside each function (same convention as the schema audit's Oracle/BigQuery adapters above), so the mock pipeline never requires them to be installed, and both authenticate via Application Default Credentials or `GOOGLE_APPLICATION_CREDENTIALS`.

- `gcs_io.py` — `upload_file`, `upload_string`, `download_file`, `download_bytes`, `download_prefix`, all operating on `gs://bucket/path` URIs.
- `bigquery_io.py` — `run_query` (parameterized query execution), `execute_script` (multi-statement scripts), `fetch_table_schema`, `load_dataframe`, and `compare_dataframe_schema` (schema-compatibility checks with type coercion for loading a DataFrame into an existing BigQuery table).

Sibling repos consume these by vendoring (byte-copying) the module with a provenance comment, since the fleet's `[vendored]`/`propagate-vendored` tooling is scoped to `project-scaffolding` as source today.

## Layout

```text
app/
  app.py                         Streamlit entry point
  views/
    welcome.py                   Overview page
    translator_demo.py           Demo, file execution, config, and result loader UI
examples/
  demo_oracle_script.sql         Fictitious Oracle script
  mapping_registry.json          Source-to-target table mapping
src/
  execution.py                   File-backed single/batch execution helpers
  connections.py                 Mock-safe connection test helpers
  gcs_io.py                      Shared real GCS upload/download helpers (ADC)
  bigquery_io.py                 Shared real BigQuery query/schema/load helpers (ADC)
  llm_client.py                  Local hub client
  mock_environment.py            SQLite mock data bootstrap
  preflight.py                   Table readiness go/no-go checks
  sql_processing.py              Materialization, splitting, table extraction
  table_registry.py              SQLite correspondence registry + CSV import/export
  translator.py                  Bounded translation function
  validation.py                  Dual execution and fingerprints
  pipelines/oracle_to_bigquery.py End-to-end orchestrator
unit_test/
  schema_compatibility_audit.py   Standalone Oracle/BQ schema audit experiment
  schema_audit_config.json        Config template for the schema audit
  query_cost_audit.py             Standalone post-migration query cost audit
  query_cost_audit_config.json    Config template for the query cost audit
  query_optimization_loop.py      Standalone bounded LLM cost-optimization loop
  query_optimization_loop_config.json Config template for the optimization loop
tests/
  test_oracle_to_bigquery.py     Mock pipeline tests
  test_schema_compatibility_audit.py Standalone schema audit tests
  test_query_cost_audit.py       Standalone query cost audit tests
  test_query_optimization_loop.py Standalone query optimization loop tests
  test_gcs_io.py                 Shared GCS IO helper tests
  test_bigquery_io.py            Shared BigQuery IO helper tests
  e2e/test_smoke.py              Streamlit boot smoke test
```

## Verification

Install Playwright browsers once if you want the full e2e gate:

```powershell
& .\.venv\Scripts\python.exe -m playwright install chromium
```

Run unit tests:

```powershell
& .\.venv\Scripts\python.exe -m pytest --ignore=tests/e2e
```

Run the full scaffold gate:

```powershell
& .\scripts\verify-before-ship.ps1
```

## Rationale

The design reasoning and tradeoffs are documented in `docs/architecture-rationale.md`. For an executive visual summary, open `docs/oracle-to-gcp-process-map.html` or the **Process Map** page in Streamlit. The short version: this project treats the LLM as one replaceable stateless function inside a deterministic migration pipeline. The hard parts are parsing, mapping, ordered execution, validation, and auditability.

`docs/build-vs-buy-migration-tooling.md` compares this pipeline against dbt, SQLMesh, sqlglot, Google Dataform, BigQuery-native cost tooling, and OpenLineage/Marquez — none of them do cross-dialect migration validation, which is this project's actual differentiator.
