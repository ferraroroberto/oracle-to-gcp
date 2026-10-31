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
import logging
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from src import bigquery_io
from src.llm_client import LocalHubClient, _strip_sql_fence
from src.validation import compare_fingerprints
from tests.audits._common import configure_logging as _configure_logging
from tests.audits._common import load_json as _load_json
from tests.audits.query_cost_audit import (
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
        log.info("Executing query against BigQuery (%d chars)", len(sql))
        return bigquery_io.run_query(sql, project=self.project_id or None)


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


def _rejected_iteration(
    iteration: int,
    sql: str,
    estimated_bytes: int,
    estimated_cost_usd: float,
    note: str,
    validation_matched: bool | None = None,
) -> OptimizationIteration:
    return OptimizationIteration(
        iteration=iteration,
        sql=sql,
        estimated_bytes_processed=estimated_bytes,
        estimated_cost_usd=estimated_cost_usd,
        accepted=False,
        validation_matched=validation_matched,
        marginal_improvement_pct=None,
        note=note,
    )


def _build_result(
    query: QueryInput,
    *,
    baseline_cost: float,
    final_cost: float,
    final_sql: str,
    stop_reason: str,
    iterations: list[OptimizationIteration],
) -> QueryOptimizationResult:
    total_improvement_pct = ((baseline_cost - final_cost) / baseline_cost * 100) if baseline_cost > 0 else 0.0
    return QueryOptimizationResult(
        query_id=query.query_id,
        order=query.order,
        statement_type=query.statement_type,
        baseline_cost_usd=baseline_cost,
        final_cost_usd=final_cost,
        final_sql=final_sql,
        total_improvement_pct=round(total_improvement_pct, 4),
        stop_reason=stop_reason,
        iterations=iterations,
    )


def _measure_baseline(
    query: QueryInput, estimator: CostEstimator, executor: QueryExecutor, price_per_tib: float
) -> tuple[OptimizationIteration, list[dict[str, Any]], str]:
    """Estimate and execute the original query; return its iteration 0, result rows and execution error."""
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
    baseline = OptimizationIteration(
        iteration=0,
        sql=query.sql,
        estimated_bytes_processed=baseline_bytes,
        estimated_cost_usd=baseline_cost,
        accepted=True,
        validation_matched=None,
        marginal_improvement_pct=None,
        note=note,
    )
    return baseline, baseline_rows, execution_error


@dataclass(slots=True)
class _CandidateOutcome:
    """A candidate's iteration record, plus why the loop must stop (empty when it can go on)."""

    iteration: OptimizationIteration
    stop_reason: str
    rows: list[dict[str, Any]]
    marginal_improvement_pct: float


def _evaluate_candidate(
    query: QueryInput,
    iteration: int,
    candidate_sql: str,
    *,
    estimator: CostEstimator,
    executor: QueryExecutor,
    price_per_tib: float,
    current_rows: list[dict[str, Any]],
    current_cost: float,
) -> _CandidateOutcome:
    """Estimate, execute and fingerprint-validate one candidate against the last accepted result set."""
    candidate_bytes, estimate_error = _safe_estimate(estimator, candidate_sql)
    if estimate_error:
        rejected = _rejected_iteration(iteration, candidate_sql, 0, 0.0, f"estimate_error: {estimate_error}")
        return _CandidateOutcome(rejected, "estimate_failed", [], 0.0)
    candidate_cost = _cost_usd(candidate_bytes, price_per_tib)

    try:
        candidate_rows = executor.execute(candidate_sql)
        execution_error = ""
    except Exception as exc:
        log.exception("Candidate execution failed for query id=%d attempt=%d", query.query_id, iteration)
        candidate_rows = []
        execution_error = f"{type(exc).__name__}: {exc}"
    if execution_error:
        rejected = _rejected_iteration(
            iteration, candidate_sql, candidate_bytes, candidate_cost, f"execution_error: {execution_error}"
        )
        return _CandidateOutcome(rejected, "execution_failed", [], 0.0)

    validation = compare_fingerprints(current_rows, candidate_rows)
    if not validation["matched"]:
        rejected = _rejected_iteration(
            iteration,
            candidate_sql,
            candidate_bytes,
            candidate_cost,
            f"validation_failed: {'; '.join(validation['diffs'])}",
            validation_matched=False,
        )
        return _CandidateOutcome(rejected, "validation_failed", [], 0.0)

    marginal_improvement_pct = ((current_cost - candidate_cost) / current_cost * 100) if current_cost > 0 else 0.0
    accepted = candidate_cost < current_cost
    evaluated = OptimizationIteration(
        iteration=iteration,
        sql=candidate_sql,
        estimated_bytes_processed=candidate_bytes,
        estimated_cost_usd=candidate_cost,
        accepted=accepted,
        validation_matched=True,
        marginal_improvement_pct=round(marginal_improvement_pct, 4),
        note="accepted" if accepted else "no_cost_improvement",
    )
    return _CandidateOutcome(evaluated, "", candidate_rows, marginal_improvement_pct)


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
    baseline, baseline_rows, execution_error = _measure_baseline(query, estimator, executor, price_per_tib)
    baseline_cost = baseline.estimated_cost_usd
    iterations = [baseline]

    if execution_error:
        return _build_result(
            query,
            baseline_cost=baseline_cost,
            final_cost=baseline_cost,
            final_sql=query.sql,
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
            note = f"llm_error: {response.error or 'empty response'}"
            iterations.append(_rejected_iteration(iteration, "", 0, 0.0, note))
            stop_reason = "llm_error"
            break

        outcome = _evaluate_candidate(
            query,
            iteration,
            candidate_sql,
            estimator=estimator,
            executor=executor,
            price_per_tib=price_per_tib,
            current_rows=current_rows,
            current_cost=current_cost,
        )
        iterations.append(outcome.iteration)
        if outcome.stop_reason:
            stop_reason = outcome.stop_reason
            break
        if outcome.iteration.accepted:
            current_sql, current_cost, current_rows = candidate_sql, outcome.iteration.estimated_cost_usd, outcome.rows
        marginal_improvement_pct = outcome.marginal_improvement_pct

        if marginal_improvement_pct < min_improvement_pct:
            flat_streak += 1
            if flat_streak >= diminishing_returns_streak:
                stop_reason = "diminishing_returns"
                break
        else:
            flat_streak = 0
    else:
        stop_reason = "max_iterations"

    return _build_result(
        query,
        baseline_cost=baseline_cost,
        final_cost=current_cost,
        final_sql=current_sql,
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


def main(argv: list[str] | None = None) -> int:
    """CLI entry point."""
    parser = argparse.ArgumentParser(description="Run the iterative BigQuery query cost-optimization loop.")
    parser.add_argument("--config", default=str(DEFAULT_CONFIG_PATH), help="Path to query optimization loop JSON config.")
    args = parser.parse_args(argv)
    run_optimization(args.config)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
