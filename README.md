# Oracle to GCP

Local prototype for translating Oracle SQL scripts into BigQuery Standard SQL through a deterministic, inspectable pipeline.

The current project is a working mock: it uses SQLite files as stand-ins for Oracle and BigQuery, ships a fictitious Oracle script, seeds both mock engines with sample data, runs translation and validation end to end, and emits a final BigQuery script plus a JSON run report. The local LLM hub is consulted as a stateless translation function when available; a deterministic fallback keeps the demo runnable offline.

## What It Demonstrates

- PL/SQL variable materialization for a simple `SELECT ... INTO` case.
- Ordered SQL unit splitting after the script has been reduced to pure SQL.
- Source-to-target table mapping through a registry.
- Input row-count parity checks for external source tables.
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

Open the **Translator Demo** page and click **Run mock translation**.

## Mock Inputs

- Oracle demo script: `examples/demo_oracle_script.sql`
- Mapping registry: `examples/mapping_registry.json`
- Runtime copies: `data/input/`
- Generated artifacts: `data/output/mock_run/`

The demo script declares `v_run_date`, resolves it from `sales_orders`, creates an intermediate `daily_revenue` table, then aggregates revenue by segment. The mock BigQuery side uses mapped tables named `raw_sales_orders` and `raw_customer_segments`, plus a scratch table named `scratch_daily_revenue`.

## Local LLM Hub

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

Expected artifacts:

- `data/output/mock_run/final_bigquery.sql`
- `data/output/mock_run/run_report_<timestamp>.json`
- `data/output/mock_run/oracle_mock.db`
- `data/output/mock_run/bigquery_mock.db`

## Layout

```text
app/
  app.py                         Streamlit entry point
  views/
    welcome.py                   Overview page
    translator_demo.py           Inspectable mock translator UI
examples/
  demo_oracle_script.sql         Fictitious Oracle script
  mapping_registry.json          Source-to-target table mapping
src/
  llm_client.py                  Local hub client
  mock_environment.py            SQLite mock data bootstrap
  sql_processing.py              Materialization, splitting, table extraction
  translator.py                  Bounded translation function
  validation.py                  Dual execution and fingerprints
  pipelines/oracle_to_bigquery.py End-to-end orchestrator
tests/
  test_oracle_to_bigquery.py     Mock pipeline tests
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

The design reasoning and tradeoffs are documented in `docs/architecture-rationale.md`. The short version: this project treats the LLM as one replaceable stateless function inside a deterministic migration pipeline. The hard parts are parsing, mapping, ordered execution, validation, and auditability.
