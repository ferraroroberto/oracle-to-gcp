"""Standalone iterative query cost-optimization loop with a correctness gate.

A direct follow-on to ``query_cost_audit.py``: instead of a one-shot
"suggest improvements" report, this module asks the local LLM hub for a
cost-optimized rewrite of each query, executes the candidate, validates it
against the previous accepted result set via ``src.validation.compare_fingerprints``,
and only adopts it if the result set is unchanged. The loop stops on a
validation failure (rolling back to the last accepted query), on diminishing
returns (marginal cost improvement below a threshold for N consecutive
iterations), or on a max-iteration cap. Like ``query_cost_audit.py`` and
``schema_compatibility_audit.py``, it is invoked as a distinct, optional step
after a migration has already validated — never from
``pipelines/oracle_to_bigquery.py``'s core loop.
"""

from __future__ import annotations

import argparse
import json
import logging
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from src.llm_client import LocalHubClient, _strip_sql_fence
from src.validation import compare_fingerprints
from unit_test.query_cost_audit import (
    BYTES_PER_TIB,
    CostEstimator,
    QueryInput,
    _build_llm_client,
    build_estimator,
    load_queries,
)

log = logging.getLogger("query_optimization_loop")

DEFAULT_CONFIG_PATH = Path(__file__).with_name("query_optimization_loop_config.json")

DEFAULT_LLM_SYSTEM_MESSAGE = (
    "You are a BigQuery cost-optimization expert. Given a validated SQL query and its "
    "estimated cost, rewrite it to reduce bytes processed while preserving identical "
    "query semantics and result set (same rows, same columns, same aggregates). "
    "Return only the rewritten SQL statement, no prose, no markdown fences."
)
DEFAULT_LLM_USER_PROMPT_TEMPLATE = (
    "Current query (iteration {iteration}, estimated cost ${current_cost_usd:.4f}):\n\n"
    "{current_sql}\n\nRewrite this query to reduce estimated cost while producing an "
    "identical result set."
)


@dataclass(slots=True)
class OptimizationIteration:
    """One iteration's candidate, its measured cost, and its accept/reject outcome."""

    iteration: int
    sql: str
    estimated_bytes_processed: int
    estimated_cost_usd: float
    accepted: bool
    validation_matched: bool | None
    marginal_improvement_pct: float | None
    note: str


@dataclass(slots=True)
class QueryOptimizationResult:
    """The full optimization history and outcome for one query."""

    query_id: int
    order: int
    statement_type: str
    baseline_cost_usd: float
    final_cost_usd: float
    final_sql: str
    total_improvement_pct: float
    stop_reason: str
    iterations: list[OptimizationIteration]


class QueryExecutor(Protocol):
    """Source of a query's materialized result rows."""

    def execute(self, sql: str) -> list[dict[str, Any]]:
        """Return result rows for one SQL statement."""


class BigQueryQueryExecutor:
    """Real BigQuery executor — issues the query and materializes result rows."""

    def __init__(self, config: dict[str, Any]) -> None:
        self.project_id = str(config.get("project_id", ""))

    def execute(self, sql: str) -> list[dict[str, Any]]:
        """Return result rows from a real BigQuery query job."""
        try:
            from google.cloud import bigquery
        except ImportError as exc:
            raise RuntimeError("Install google-cloud-bigquery to use the BigQuery query executor") from exc

        client = bigquery.Client(project=self.project_id or None)
        log.info("Executing query against BigQuery (%d chars)", len(sql))
        return [dict(row) for row in client.query(sql).result()]


class SqliteQueryExecutor:
    """SQLite executor for local experimentation and tests.

    Runs SQL directly against a mock database with no BigQuery-dialect translation —
    unlike the main pipeline's SQLite mock, this loop assumes candidates are already
    valid SQL for whichever engine ``db_path`` represents.
    """

    def __init__(self, config: dict[str, Any]) -> None:
        self.db_path = str(config.get("db_path", ""))

    def execute(self, sql: str) -> list[dict[str, Any]]:
        """Return result rows from the configured SQLite database."""
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.execute(sql)
            rows = [dict(row) for row in cursor.fetchall()]
            conn.commit()
            return rows


def build_executor(config: dict[str, Any]) -> QueryExecutor:
    """Build the configured query executor."""
    mode = str(config.get("mode", "sqlite")).lower()
    if mode == "bigquery":
        return BigQueryQueryExecutor(config)
    if mode == "sqlite":
        return SqliteQueryExecutor(config)
    raise ValueError(f"Unsupported query executor mode: {mode}")


def run_optimization(
    config_path: str | Path = DEFAULT_CONFIG_PATH,
    llm_client: LocalHubClient | None = None,
) -> list[QueryOptimizationResult]:
    """Run the configured optimization loop for every query and write the report."""
    config = _load_json(config_path)
    _configure_logging(config.get("logging", {}))
    script_id, queries = load_queries(config.get("input", {}))
    estimator = build_estimator(config.get("estimator", {}))
    executor = build_executor(config.get("executor", {}))
    price_per_tib = float(config.get("pricing", {}).get("price_per_tib_usd", 6.25))
    log.info("Loaded %d translated statement(s) for run %s", len(queries), script_id)

    optimization_config = config.get("optimization", {})
    max_iterations = int(optimization_config.get("max_iterations", 5))
    min_improvement_pct = float(optimization_config.get("min_improvement_pct", 2.0))
    diminishing_returns_streak = int(optimization_config.get("diminishing_returns_streak", 2))

    llm_config = config.get("llm", {})
    llm_enabled = bool(llm_config.get("enabled", True))
    client = (llm_client or _build_llm_client(llm_config)) if llm_enabled else None
    effective_max_iterations = max_iterations if llm_enabled else 0

    results = [
        optimize_query(
            query,
            estimator=estimator,
            executor=executor,
            price_per_tib=price_per_tib,
            llm_client=client,
            llm_config=llm_config,
            max_iterations=effective_max_iterations,
            min_improvement_pct=min_improvement_pct,
            diminishing_returns_streak=diminishing_returns_streak,
        )
        for query in queries
    ]

    report_path = Path(
        str(config.get("output", {}).get("report_md", "data/output/query_cost_audit/optimization_report.md"))
    )
    write_report(report_path, script_id, results)
    log.info("Wrote query optimization report: %s", report_path)
    return results


def optimize_query(
    query: QueryInput,
    *,
    estimator: CostEstimator,
    executor: QueryExecutor,
    price_per_tib: float,
    llm_client: LocalHubClient | None,
    llm_config: dict[str, Any],
    max_iterations: int,
    min_improvement_pct: float,
    diminishing_returns_streak: int,
) -> QueryOptimizationResult:
    """Run the bounded optimize-execute-validate loop for one query."""
    baseline_bytes, estimate_error = _safe_estimate(estimator, query.sql)
    baseline_cost = _cost_usd(baseline_bytes, price_per_tib)
    try:
        baseline_rows = executor.execute(query.sql)
        execution_error = ""
    except Exception as exc:
        log.exception("Baseline execution failed for query id=%d", query.query_id)
        baseline_rows = []
        execution_error = f"{type(exc).__name__}: {exc}"

    note = "baseline"
    if estimate_error:
        note += f" (estimate error: {estimate_error})"
    if execution_error:
        note += f" (execution error: {execution_error})"
    iterations = [
        OptimizationIteration(
            iteration=0,
            sql=query.sql,
            estimated_bytes_processed=baseline_bytes,
            estimated_cost_usd=baseline_cost,
            accepted=True,
            validation_matched=None,
            marginal_improvement_pct=None,
            note=note,
        )
    ]

    if execution_error:
        return QueryOptimizationResult(
            query_id=query.query_id,
            order=query.order,
            statement_type=query.statement_type,
            baseline_cost_usd=baseline_cost,
            final_cost_usd=baseline_cost,
            final_sql=query.sql,
            total_improvement_pct=0.0,
            stop_reason="baseline_execution_failed",
            iterations=iterations,
        )

    current_sql, current_cost, current_rows = query.sql, baseline_cost, baseline_rows
    flat_streak = 0
    stop_reason = "max_iterations"

    for iteration in range(1, max_iterations + 1):
        assert llm_client is not None  # max_iterations is 0 whenever llm_client is None
        response = llm_client.complete(
            str(llm_config.get("system_message", DEFAULT_LLM_SYSTEM_MESSAGE)),
            str(llm_config.get("user_prompt_template", DEFAULT_LLM_USER_PROMPT_TEMPLATE)).format(
                iteration=iteration,
                current_sql=current_sql,
                current_cost_usd=current_cost,
            ),
        )
        candidate_sql = _strip_sql_fence(response.text).strip() if not response.error else ""
        if response.error or not candidate_sql:
            iterations.append(
                OptimizationIteration(
                    iteration=iteration,
                    sql="",
                    estimated_bytes_processed=0,
                    estimated_cost_usd=0.0,
                    accepted=False,
                    validation_matched=None,
                    marginal_improvement_pct=None,
                    note=f"llm_error: {response.error or 'empty response'}",
                )
            )
            stop_reason = "llm_error"
            break

        candidate_bytes, estimate_error = _safe_estimate(estimator, candidate_sql)
        if estimate_error:
            iterations.append(
                OptimizationIteration(
                    iteration=iteration,
                    sql=candidate_sql,
                    estimated_bytes_processed=0,
                    estimated_cost_usd=0.0,
                    accepted=False,
                    validation_matched=None,
                    marginal_improvement_pct=None,
                    note=f"estimate_error: {estimate_error}",
                )
            )
            stop_reason = "estimate_failed"
            break
        candidate_cost = _cost_usd(candidate_bytes, price_per_tib)

        try:
            candidate_rows = executor.execute(candidate_sql)
            execution_error = ""
        except Exception as exc:
            log.exception("Candidate execution failed for query id=%d attempt=%d", query.query_id, iteration)
            candidate_rows = []
            execution_error = f"{type(exc).__name__}: {exc}"
        if execution_error:
            iterations.append(
                OptimizationIteration(
                    iteration=iteration,
                    sql=candidate_sql,
                    estimated_bytes_processed=candidate_bytes,
                    estimated_cost_usd=candidate_cost,
                    accepted=False,
                    validation_matched=None,
                    marginal_improvement_pct=None,
                    note=f"execution_error: {execution_error}",
                )
            )
            stop_reason = "execution_failed"
            break

        validation = compare_fingerprints(current_rows, candidate_rows)
        if not validation["matched"]:
            iterations.append(
                OptimizationIteration(
                    iteration=iteration,
                    sql=candidate_sql,
                    estimated_bytes_processed=candidate_bytes,
                    estimated_cost_usd=candidate_cost,
                    accepted=False,
                    validation_matched=False,
                    marginal_improvement_pct=None,
                    note=f"validation_failed: {'; '.join(validation['diffs'])}",
                )
            )
            stop_reason = "validation_failed"
            break

        marginal_improvement_pct = ((current_cost - candidate_cost) / current_cost * 100) if current_cost > 0 else 0.0
        accepted = candidate_cost < current_cost
        iterations.append(
            OptimizationIteration(
                iteration=iteration,
                sql=candidate_sql,
                estimated_bytes_processed=candidate_bytes,
                estimated_cost_usd=candidate_cost,
                accepted=accepted,
                validation_matched=True,
                marginal_improvement_pct=round(marginal_improvement_pct, 4),
                note="accepted" if accepted else "no_cost_improvement",
            )
        )
        if accepted:
            current_sql, current_cost, current_rows = candidate_sql, candidate_cost, candidate_rows

        if marginal_improvement_pct < min_improvement_pct:
            flat_streak += 1
            if flat_streak >= diminishing_returns_streak:
                stop_reason = "diminishing_returns"
                break
        else:
            flat_streak = 0
    else:
        stop_reason = "max_iterations"

    total_improvement_pct = ((baseline_cost - current_cost) / baseline_cost * 100) if baseline_cost > 0 else 0.0
    return QueryOptimizationResult(
        query_id=query.query_id,
        order=query.order,
        statement_type=query.statement_type,
        baseline_cost_usd=baseline_cost,
        final_cost_usd=current_cost,
        final_sql=current_sql,
        total_improvement_pct=round(total_improvement_pct, 4),
        stop_reason=stop_reason,
        iterations=iterations,
    )


def write_report(path: Path, script_id: str, results: list[QueryOptimizationResult]) -> None:
    """Write the per-query iteration history and overall improvement as markdown."""
    total_baseline = sum(result.baseline_cost_usd for result in results)
    total_final = sum(result.final_cost_usd for result in results)
    overall_pct = ((total_baseline - total_final) / total_baseline * 100) if total_baseline > 0 else 0.0

    lines = [
        f"# Query Optimization Loop — {script_id}",
        "",
        "## Summary",
        "",
        f"- Queries optimized: {len(results)}",
        f"- Total baseline cost: ${total_baseline:.4f}",
        f"- Total final cost: ${total_final:.4f}",
        f"- Overall improvement: {overall_pct:.2f}%",
        "",
    ]
    for result in results:
        lines.extend(
            [
                f"## Query {result.query_id} (order {result.order}, {result.statement_type})",
                "",
                f"- Baseline cost: ${result.baseline_cost_usd:.4f}",
                f"- Final cost: ${result.final_cost_usd:.4f}",
                f"- Improvement: {result.total_improvement_pct:.2f}%",
                f"- Stop reason: {result.stop_reason}",
                "",
                "| Iteration | Accepted | Validated | Marginal % | Est. Cost (USD) | Note |",
                "|---|---|---|---|---|---|",
            ]
        )
        for it in result.iterations:
            validated = "" if it.validation_matched is None else ("yes" if it.validation_matched else "no")
            marginal = "" if it.marginal_improvement_pct is None else f"{it.marginal_improvement_pct:.2f}"
            lines.append(
                f"| {it.iteration} | {'yes' if it.accepted else 'no'} | {validated} | {marginal} | "
                f"${it.estimated_cost_usd:.4f} | {it.note} |"
            )
        lines.extend(["", "### Final query", "", "```sql", result.final_sql, "```", ""])

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8")


def _safe_estimate(estimator: CostEstimator, sql: str) -> tuple[int, str]:
    try:
        return estimator.estimate_bytes(sql), ""
    except Exception as exc:
        log.exception("Cost estimation failed")
        return 0, f"{type(exc).__name__}: {exc}"


def _cost_usd(bytes_processed: int, price_per_tib: float) -> float:
    return (bytes_processed / BYTES_PER_TIB) * price_per_tib


def _load_json(path: str | Path) -> dict[str, Any]:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _configure_logging(config: dict[str, Any]) -> None:
    level_name = str(config.get("level", "INFO")).upper()
    logging.basicConfig(level=getattr(logging, level_name, logging.INFO), format="%(asctime)s %(levelname)s %(message)s")


def main(argv: list[str] | None = None) -> int:
    """CLI entry point."""
    parser = argparse.ArgumentParser(description="Run the iterative BigQuery query cost-optimization loop.")
    parser.add_argument("--config", default=str(DEFAULT_CONFIG_PATH), help="Path to query optimization loop JSON config.")
    args = parser.parse_args(argv)
    run_optimization(args.config)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
