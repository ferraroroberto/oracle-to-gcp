"""Standalone post-migration BigQuery query cost audit.

Consumes a completed run's translated BigQuery statements (``RunReport.units``,
``src/sql_models.py``), estimates the execution cost of each one, ranks the
most expensive queries, writes a markdown report, and appends an LLM-generated
optimization-suggestions section via the local hub client
(``src/llm_client.py``). This mirrors ``schema_compatibility_audit.py``'s
config-driven, adapter-based shape: it runs against a real BigQuery connection
(dry-run query stats), not the SQLite mock, since cost has no SQLite
equivalent. It is invoked as a distinct, optional step after validation
passes — never from ``pipelines/oracle_to_bigquery.py``'s core loop.
"""

from __future__ import annotations

import argparse
import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from src.config import LLM_BASE_URL, LLM_MODEL, LLM_TIMEOUT_SECONDS
from src.llm_client import LocalHubClient
from unit_test._common import configure_logging as _configure_logging
from unit_test._common import load_json as _load_json

log = logging.getLogger("query_cost_audit")

DEFAULT_CONFIG_PATH = Path(__file__).with_name("query_cost_audit_config.json")
BYTES_PER_TIB = 2**40

DEFAULT_LLM_SYSTEM_MESSAGE = (
    "You are a BigQuery cost-optimization expert reviewing a completed Oracle-to-BigQuery "
    "migration. You are given a ranked list of queries and their estimated bytes-processed "
    "cost. Suggest concrete optimizations (partitioning, clustering, query rewrites, "
    "pruning unnecessary scans) for the most expensive queries. Be specific and reference "
    "query rank numbers. Return markdown bullet points only, no headings."
)
DEFAULT_LLM_USER_PROMPT_TEMPLATE = (
    "Here is the query cost report for run '{script_id}' (total estimated cost: "
    "${total_cost_usd:.4f}):\n\n{report_table}\n\nSuggest optimizations for the most "
    "expensive queries."
)


@dataclass(slots=True)
class QueryInput:
    """One SQL statement to estimate cost for."""

    query_id: int
    order: int
    statement_type: str
    sql: str


@dataclass(slots=True)
class CostEstimate:
    """One query's estimated cost, ranked against its run siblings."""

    rank: int
    query_id: int
    order: int
    statement_type: str
    sql_preview: str
    estimated_bytes_processed: int
    estimated_cost_usd: float
    error: str = ""


class CostEstimator(Protocol):
    """Source of a query's estimated bytes processed."""

    def estimate_bytes(self, sql: str) -> int:
        """Return estimated bytes processed for one SQL statement."""


class BigQueryDryRunEstimator:
    """Real BigQuery dry-run estimator — no data is scanned or returned."""

    def __init__(self, config: dict[str, Any]) -> None:
        self.project_id = str(config.get("project_id", ""))

    def estimate_bytes(self, sql: str) -> int:
        """Return ``total_bytes_processed`` from a dry-run query job."""
        try:
            from google.cloud import bigquery
        except ImportError as exc:
            raise RuntimeError("Install google-cloud-bigquery to use the BigQuery cost estimator") from exc

        client = bigquery.Client(project=self.project_id or None)
        job_config = bigquery.QueryJobConfig(dry_run=True, use_query_cache=False)
        log.info("Dry-running query against BigQuery (%d chars)", len(sql))
        job = client.query(sql, job_config=job_config)
        return int(job.total_bytes_processed or 0)


class MockCostEstimator:
    """Deterministic placeholder estimator for local experimentation without live BigQuery.

    Estimates bytes processed as a fixed multiple of the query text length. This has no
    relation to actual scan volume — it exists only so the ranking/report/LLM machinery is
    exercisable without real credentials, matching this project's mock-first convention.
    """

    def __init__(self, config: dict[str, Any]) -> None:
        self.bytes_per_char = int(config.get("bytes_per_char", 1024))

    def estimate_bytes(self, sql: str) -> int:
        """Return a deterministic byte estimate proportional to SQL text length."""
        return len(sql.encode("utf-8")) * self.bytes_per_char


def run_audit(
    config_path: str | Path = DEFAULT_CONFIG_PATH,
    llm_client: LocalHubClient | None = None,
) -> list[CostEstimate]:
    """Run the configured cost audit and write the markdown report."""
    config = _load_json(config_path)
    _configure_logging(config.get("logging", {}))
    script_id, queries = load_queries(config.get("input", {}))
    estimator = build_estimator(config.get("estimator", {}))
    price_per_tib = float(config.get("pricing", {}).get("price_per_tib_usd", 6.25))
    log.info("Loaded %d translated statement(s) for run %s", len(queries), script_id)

    estimates = estimate_costs(queries, estimator, price_per_tib)
    total_cost_usd = sum(estimate.estimated_cost_usd for estimate in estimates)

    suggestions = ""
    llm_config = config.get("llm", {})
    if bool(llm_config.get("enabled", True)):
        client = llm_client or _build_llm_client(llm_config)
        suggestions = generate_suggestions(script_id, estimates, total_cost_usd, client, llm_config)
    else:
        log.info("LLM optimization pass disabled by config")

    report_path = Path(str(config.get("output", {}).get("report_md", "data/output/query_cost_audit/cost_report.md")))
    write_report(report_path, script_id, estimates, total_cost_usd, suggestions)
    log.info("Wrote query cost report: %s", report_path)
    return estimates


def load_queries(config: dict[str, Any]) -> tuple[str, list[QueryInput]]:
    """Load translated statements from a run report or a plain JSON list."""
    input_path = Path(str(config.get("path", "")))
    if not input_path.exists():
        raise FileNotFoundError(f"Query cost audit input not found: {input_path}")
    input_format = str(config.get("format", "run_report")).lower()
    raw = json.loads(input_path.read_text(encoding="utf-8"))

    if input_format == "run_report":
        script_id = str(raw.get("script_id", input_path.stem))
        units = raw.get("units", [])
        queries = [
            QueryInput(
                query_id=int(unit.get("id", 0)),
                order=int(unit.get("order", 0)),
                statement_type=str(unit.get("statement_type", "")),
                sql=str(unit.get("bq_sql", "")),
            )
            for unit in units
            if str(unit.get("bq_sql", "")).strip()
        ]
    elif input_format == "json":
        script_id = str(config.get("script_id", input_path.stem))
        rows = raw if isinstance(raw, list) else raw.get("queries", [])
        queries = [
            QueryInput(
                query_id=int(row.get("id", index)),
                order=int(row.get("order", index)),
                statement_type=str(row.get("statement_type", "")),
                sql=str(row.get("sql", "")),
            )
            for index, row in enumerate(rows, start=1)
            if str(row.get("sql", "")).strip()
        ]
    else:
        raise ValueError(f"Unsupported input format: {input_format}")

    return script_id, sorted(queries, key=lambda query: query.order)


def build_estimator(config: dict[str, Any]) -> CostEstimator:
    """Build the configured cost estimator."""
    mode = str(config.get("mode", "mock")).lower()
    if mode == "bigquery":
        return BigQueryDryRunEstimator(config)
    if mode == "mock":
        return MockCostEstimator(config)
    raise ValueError(f"Unsupported cost estimator mode: {mode}")


def estimate_costs(
    queries: list[QueryInput],
    estimator: CostEstimator,
    price_per_tib: float,
) -> list[CostEstimate]:
    """Estimate and rank cost for each query, most expensive first."""
    raw_estimates: list[CostEstimate] = []
    for query in queries:
        try:
            bytes_processed = estimator.estimate_bytes(query.sql)
            error = ""
        except Exception as exc:
            log.exception("Cost estimation failed for query id=%d", query.query_id)
            bytes_processed = 0
            error = f"{type(exc).__name__}: {exc}"
        cost_usd = (bytes_processed / BYTES_PER_TIB) * price_per_tib
        raw_estimates.append(
            CostEstimate(
                rank=0,
                query_id=query.query_id,
                order=query.order,
                statement_type=query.statement_type,
                sql_preview=_preview(query.sql),
                estimated_bytes_processed=bytes_processed,
                estimated_cost_usd=cost_usd,
                error=error,
            )
        )
    ranked = sorted(raw_estimates, key=lambda estimate: estimate.estimated_cost_usd, reverse=True)
    for position, estimate in enumerate(ranked, start=1):
        estimate.rank = position
    return ranked


def generate_suggestions(
    script_id: str,
    estimates: list[CostEstimate],
    total_cost_usd: float,
    client: LocalHubClient,
    llm_config: dict[str, Any],
) -> str:
    """Ask the local hub for optimization suggestions on the ranked report."""
    user_prompt_template = str(llm_config.get("user_prompt_template", DEFAULT_LLM_USER_PROMPT_TEMPLATE))
    system_message = str(llm_config.get("system_message", DEFAULT_LLM_SYSTEM_MESSAGE))
    prompt = user_prompt_template.format(
        script_id=script_id,
        total_cost_usd=total_cost_usd,
        report_table=_render_table(estimates),
    )
    response = client.complete(system_message, prompt)
    if response.error:
        log.warning("LLM optimization pass failed: %s", response.error)
        return f"_LLM optimization pass failed: {response.error}_"
    return response.text


def write_report(
    path: Path,
    script_id: str,
    estimates: list[CostEstimate],
    total_cost_usd: float,
    suggestions: str,
) -> None:
    """Write the ranked cost report and optimization suggestions as markdown."""
    total_bytes = sum(estimate.estimated_bytes_processed for estimate in estimates)
    failed = sum(1 for estimate in estimates if estimate.error)
    lines = [
        f"# Query Cost Audit — {script_id}",
        "",
        "## Summary",
        "",
        f"- Queries analyzed: {len(estimates)} ({failed} failed)",
        f"- Total estimated bytes processed: {total_bytes:,}",
        f"- Total estimated cost: ${total_cost_usd:.4f}",
        "",
        "## Ranked by estimated cost",
        "",
        _render_table(estimates),
        "",
        "## Optimization suggestions",
        "",
        suggestions or "_No optimization suggestions generated._",
        "",
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8")


def _render_table(estimates: list[CostEstimate]) -> str:
    header = "| Rank | Order | Statement Type | Estimated Bytes | Estimated Cost (USD) | SQL preview |"
    divider = "|---|---|---|---|---|---|"
    rows = [
        f"| {estimate.rank} | {estimate.order} | {estimate.statement_type} | "
        f"{estimate.estimated_bytes_processed:,} | ${estimate.estimated_cost_usd:.4f} | "
        f"{estimate.sql_preview}{' (error: ' + estimate.error + ')' if estimate.error else ''} |"
        for estimate in estimates
    ]
    return "\n".join([header, divider, *rows])


def _preview(sql: str, limit: int = 80) -> str:
    collapsed = " ".join(sql.split())
    truncated = collapsed[:limit] + ("…" if len(collapsed) > limit else "")
    return truncated.replace("|", "\\|")


def _build_llm_client(config: dict[str, Any]) -> LocalHubClient:
    return LocalHubClient(
        base_url=str(config.get("base_url", LLM_BASE_URL)),
        model=str(config.get("model", LLM_MODEL)),
        timeout=float(config.get("timeout_seconds", LLM_TIMEOUT_SECONDS)),
    )


def main(argv: list[str] | None = None) -> int:
    """CLI entry point."""
    parser = argparse.ArgumentParser(description="Audit post-migration BigQuery query cost.")
    parser.add_argument("--config", default=str(DEFAULT_CONFIG_PATH), help="Path to query cost audit JSON config.")
    args = parser.parse_args(argv)
    run_audit(args.config)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
