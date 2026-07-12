"""End-to-end Oracle-to-BigQuery mock translation pipeline."""

from __future__ import annotations

import argparse
import json
from collections.abc import Callable
from contextlib import closing
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from src import get_logger
from src.lineage import render_lineage_markdown
from src.mock_environment import (
    bootstrap_mock_environment,
    connect_sqlite,
    load_demo_script,
    load_mapping_registry,
)
from src.pipeline_config import (
    PipelineConfig,
    load_pipeline_config,
    resolve_output_dir,
    with_overrides,
)
from src.preflight import run_table_preflight
from src.sql_models import RunReport, SqlUnit
from src.sql_processing import build_units, materialize_variables
from src.trace import TraceRecorder
from src.translator import TranslationEngine
from src.validation import (
    assert_rowcount_parity,
    compare_fingerprints,
    execute_bigquery_unit,
    execute_oracle_unit,
)

log = get_logger("oracle_to_bigquery")


def run(
    *,
    script: str | None = None,
    mapping: dict[str, str] | None = None,
    config_path: Path | str | None = None,
    pipeline_config: PipelineConfig | None = None,
    use_local_hub: bool | None = None,
    repair_limit: int | None = None,
    simulate_repair_path: bool | None = None,
    trace_enabled: bool | None = None,
    trace_verbose: bool | None = None,
    run_dir: Path | None = None,
) -> RunReport:
    """Run the full mock translation, execution, validation, and report flow."""
    active_config = pipeline_config or load_pipeline_config(config_path)
    active_config = with_overrides(
        active_config,
        use_local_hub=use_local_hub,
        repair_limit=repair_limit,
        simulate_repair_path=simulate_repair_path,
        trace_enabled=trace_enabled,
        trace_verbose=trace_verbose,
        output_dir=str(run_dir) if run_dir is not None else None,
    )
    recorder = TraceRecorder(active_config.trace)
    paths = bootstrap_mock_environment(resolve_output_dir(active_config.run.output_dir))
    mapping_registry = mapping or load_mapping_registry()
    source_script = script or load_demo_script()
    run_log: list[str] = []

    def record(message: str, *args: object) -> None:
        rendered = message % args if args else message
        run_log.append(rendered)
        log.info(rendered)

    recorder.add(
        "run",
        "started",
        {
            "config_path": active_config.path,
            "use_local_hub": active_config.run.use_local_hub,
            "repair_limit": active_config.run.repair_limit,
            "simulate_repair_path": active_config.run.simulate_repair_path,
            "trace_verbose": active_config.trace.verbose,
        },
    )
    record("Stage 0 ingest: mock Oracle=%s mock BigQuery=%s", paths["oracle_db"], paths["bigquery_db"])
    recorder.add("ingest", "mock_environment_bootstrapped", _stringify_paths(paths))

    timestamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    artifact_dir = Path(paths["run_dir"])
    trace_path = artifact_dir / f"run_trace_{timestamp}.json"
    try:
        with (
            closing(connect_sqlite(paths["oracle_db"])) as oracle_conn,
            closing(connect_sqlite(paths["bigquery_db"])) as bq_conn,
        ):
            pure_sql, resolved = materialize_variables(source_script, oracle_conn)
            record("Stage 1 materialized variables: %s", resolved)
            recorder.add(
                "materialize",
                "variables_resolved",
                {
                    "resolved_variables": resolved,
                    "pure_sql": pure_sql if active_config.trace.verbose else "",
                },
            )

            units = build_units(pure_sql)
            record("Stage 2 split script into %d ordered units", len(units))
            recorder.add("split", "units_built", {"unit_count": len(units), "units": [_unit_summary(unit) for unit in units]})

            preflight = run_table_preflight(
                units=units,
                pipeline_config=active_config,
                legacy_mapping=mapping_registry,
                oracle_conn=oracle_conn,
                bigquery_conn=bq_conn,
                registry_path=active_config.execution.table_registry_path,
            )
            record("Stage 3 table preflight: %s", "; ".join(preflight.messages))
            recorder.add("preflight", "table_readiness_checked", preflight.to_dict())
            if not preflight.can_run:
                raise ValueError("table preflight failed: " + "; ".join(preflight.messages))

            registry_mapping = {row.oracle_table: row.bigquery_table for row in preflight.mappings if row.ready}
            intermediate_mapping = _target_mapping(units)
            active_mapping = {**mapping_registry, **registry_mapping, **intermediate_mapping}
            _assert_all_sources_mapped(units, active_mapping)
            recorder.add(
                "mapping",
                "active_mapping_ready",
                {
                    "registry": mapping_registry,
                    "registry_ready_mapping": registry_mapping,
                    "intermediate_mapping": intermediate_mapping,
                    "active_mapping": active_mapping,
                },
            )
            for unit in units:
                external_sources = [source for source in unit.sources if source in registry_mapping]
                checks = assert_rowcount_parity(oracle_conn, bq_conn, registry_mapping, external_sources)
                if checks:
                    record("Stage 4 row-count parity for unit %d: %s", unit.id, checks)
                    recorder.add("validation", "rowcount_parity", {"unit_id": unit.id, "checks": checks})

            translator = TranslationEngine(
                use_local_hub=active_config.run.use_local_hub,
                simulate_first_attempt_mismatch=active_config.run.simulate_repair_path,
                llm_config=active_config.llm,
            )
            for unit in units:
                _translate_execute_validate(
                    unit=unit,
                    translator=translator,
                    mapping=active_mapping,
                    oracle_conn=oracle_conn,
                    bq_conn=bq_conn,
                    repair_limit=active_config.run.repair_limit,
                    record=record,
                    recorder=recorder,
                )
    except Exception as exc:
        recorder.add(
            "run",
            "failed",
            {"error_type": type(exc).__name__, "error": str(exc)},
        )
        recorder.write(
            trace_path,
            status="failed",
            pipeline_config=active_config,
            artifacts={"trace_json": str(trace_path)},
        )
        raise

    final_script = "\n\n".join(unit.bq_sql.rstrip(";") + ";" for unit in units)
    script_path = artifact_dir / "final_bigquery.sql"
    report_path = artifact_dir / f"run_report_{timestamp}.json"
    lineage_path = artifact_dir / "lineage.md"
    script_id = f"demo-{timestamp}"
    script_path.write_text(final_script + "\n", encoding="utf-8")
    lineage_path.write_text(render_lineage_markdown(units, script_id=script_id), encoding="utf-8")

    status = "validated" if all(unit.status == "validated" for unit in units) else "flagged"
    artifacts = {
        "final_bigquery_sql": str(script_path),
        "run_report_json": str(report_path),
        "lineage_md": str(lineage_path),
        "oracle_mock_db": str(paths["oracle_db"]),
        "bigquery_mock_db": str(paths["bigquery_db"]),
    }
    if active_config.trace.enabled:
        artifacts["run_trace_json"] = str(trace_path)
    report = RunReport(
        script_id=script_id,
        status=status,
        resolved_variables=resolved,
        units=units,
        final_bigquery_script=final_script,
        artifacts=artifacts,
        log=run_log,
        trace=recorder.to_list(),
    )
    record("Stage 8 artifacts written: %s", artifact_dir)
    recorder.add("artifacts", "written", artifacts)
    recorder.write(trace_path, status=status, pipeline_config=active_config, artifacts=artifacts)
    report_path.write_text(json.dumps(_report_to_jsonable(report), indent=2), encoding="utf-8")
    return report


def _translate_execute_validate(
    *,
    unit: SqlUnit,
    translator: TranslationEngine,
    mapping: dict[str, str],
    oracle_conn: Any,
    bq_conn: Any,
    repair_limit: int,
    record: Callable[..., None],
    recorder: TraceRecorder,
) -> None:
    for attempt in range(repair_limit + 1):
        recorder.add(
            "translation",
            "attempt_started",
            {
                "unit_id": unit.id,
                "attempt": attempt,
                "oracle_sql": unit.pure_oracle,
                "mapping": mapping,
            },
        )
        unit.bq_sql, unit.translator = translator.translate(unit.pure_oracle, mapping, attempt=attempt)
        _trace_llm_response(recorder, unit.id, attempt, translator.last_llm_response)
        recorder.add(
            "translation",
            "attempt_finished",
            {
                "unit_id": unit.id,
                "attempt": attempt,
                "translator": unit.translator,
                "note": translator.last_note,
                "bigquery_sql": unit.bq_sql,
            },
        )
        if attempt == 0:
            record("Stage 4 translated unit %d via %s (%s)", unit.id, unit.translator, translator.last_note)
        else:
            record("Stage 7 repair attempt %d for unit %d via %s", attempt, unit.id, unit.translator)

        oracle_rows = execute_oracle_unit(oracle_conn, unit.pure_oracle)
        recorder.add(
            "execution",
            "oracle_unit_executed",
            {"unit_id": unit.id, "attempt": attempt, "sql": unit.pure_oracle, **recorder.rows(oracle_rows)},
        )
        bq_rows = execute_bigquery_unit(bq_conn, unit.bq_sql)
        recorder.add(
            "execution",
            "bigquery_unit_executed",
            {"unit_id": unit.id, "attempt": attempt, "sql": unit.bq_sql, **recorder.rows(bq_rows)},
        )
        validation = compare_fingerprints(oracle_rows, bq_rows)
        unit.validation_result = validation
        recorder.add(
            "validation",
            "fingerprints_compared",
            {"unit_id": unit.id, "attempt": attempt, "validation": validation},
        )

        if validation["matched"]:
            unit.status = "validated"
            unit.repair_attempts = attempt
            record("Stage 6 validated unit %d after %d repair attempts", unit.id, attempt)
            return

        record("Stage 6 mismatch for unit %d: %s", unit.id, validation["diffs"])

    unit.status = "flagged"
    unit.repair_attempts = repair_limit
    record("Stage 7 flagged unit %d for human review after %d attempts", unit.id, repair_limit)


def _target_mapping(units: list[SqlUnit]) -> dict[str, str]:
    mapping: dict[str, str] = {}
    for unit in units:
        for target in unit.targets:
            mapping[target] = f"scratch_{target}"
    return mapping


def _assert_all_sources_mapped(units: list[SqlUnit], mapping: dict[str, str]) -> None:
    missing = sorted({source for unit in units for source in unit.sources if source not in mapping})
    if missing:
        joined = ", ".join(missing)
        raise ValueError(f"unmapped source table(s): {joined}")


def _report_to_jsonable(report: RunReport) -> dict[str, Any]:
    return asdict(report)


def _unit_summary(unit: SqlUnit) -> dict[str, Any]:
    return {
        "id": unit.id,
        "order": unit.order,
        "statement_type": unit.statement_type,
        "sources": unit.sources,
        "targets": unit.targets,
        "pure_oracle": unit.pure_oracle,
    }


def _stringify_paths(paths: dict[str, Path]) -> dict[str, str]:
    return {key: str(value) for key, value in paths.items()}


def _trace_llm_response(
    recorder: TraceRecorder,
    unit_id: int,
    attempt: int,
    response: Any,
) -> None:
    if response is None:
        return
    details = {
        "unit_id": unit_id,
        "attempt": attempt,
        "provider": response.provider,
        "endpoint": response.endpoint,
        "duration_ms": response.duration_ms,
        "error": response.error,
        "text": response.text,
    }
    if recorder.config.capture_llm_payloads:
        details["request_payload"] = response.request_payload
        details["response_payload"] = response.response_payload
    recorder.add("llm", "call_finished", details)


def main(argv: list[str] | None = None) -> int:
    """CLI entry point for the mock pipeline."""
    parser = argparse.ArgumentParser(description="Run the Oracle-to-BigQuery mock pipeline.")
    parser.add_argument("--config", default=None, help="Path to a pipeline JSON config file.")
    parser.add_argument("--output-dir", default=None, help="Override the configured output directory.")
    parser.add_argument("--input-sql", default=None, help="Run one .sql file through file-backed execution.")
    parser.add_argument("--input-dir", default=None, help="Directory for batch .sql execution.")
    parser.add_argument("--batch", action="store_true", help="Batch-run .sql files from --input-dir or config.")
    parser.add_argument("--result-suffix", default=None, help="Sibling result directory suffix, default from config.")
    parser.add_argument("--use-local-hub", action=argparse.BooleanOptionalAction, default=None)
    parser.add_argument("--simulate-repair-path", action=argparse.BooleanOptionalAction, default=None)
    parser.add_argument("--repair-limit", type=int, default=None)
    parser.add_argument("--trace", action=argparse.BooleanOptionalAction, default=None)
    parser.add_argument("--trace-verbose", action=argparse.BooleanOptionalAction, default=None)
    parser.add_argument("--trace-capture-llm-payloads", action=argparse.BooleanOptionalAction, default=None)
    parser.add_argument("--trace-capture-query-results", action=argparse.BooleanOptionalAction, default=None)
    parser.add_argument("--trace-max-query-rows", type=int, default=None)
    parser.add_argument("--llm-base-url", default=None)
    parser.add_argument("--llm-model", default=None)
    parser.add_argument("--llm-timeout-seconds", type=float, default=None)
    parser.add_argument("--llm-temperature", type=float, default=None)
    parser.add_argument("--llm-system-message", default=None)
    parser.add_argument("--llm-user-prompt-template", default=None)
    parser.add_argument(
        "--llm-extra-json",
        default=None,
        help="JSON object merged as extra OpenAI-shape model parameters.",
    )
    args = parser.parse_args(argv)
    try:
        llm_extra_parameters = json.loads(args.llm_extra_json) if args.llm_extra_json else None
    except json.JSONDecodeError as exc:
        parser.error(f"--llm-extra-json must be valid JSON: {exc}")
    if llm_extra_parameters is not None and not isinstance(llm_extra_parameters, dict):
        parser.error("--llm-extra-json must be a JSON object")

    config = with_overrides(
        load_pipeline_config(args.config),
        use_local_hub=args.use_local_hub,
        simulate_repair_path=args.simulate_repair_path,
        repair_limit=args.repair_limit,
        output_dir=args.output_dir,
        execution_input_dir=args.input_dir,
        execution_result_suffix=args.result_suffix,
        trace_enabled=args.trace,
        trace_verbose=args.trace_verbose,
        trace_capture_llm_payloads=args.trace_capture_llm_payloads,
        trace_capture_query_results=args.trace_capture_query_results,
        trace_max_query_rows=args.trace_max_query_rows,
        llm_base_url=args.llm_base_url,
        llm_model=args.llm_model,
        llm_timeout_seconds=args.llm_timeout_seconds,
        llm_temperature=args.llm_temperature,
        llm_system_message=args.llm_system_message,
        llm_user_prompt_template=args.llm_user_prompt_template,
        llm_extra_parameters=llm_extra_parameters,
    )
    if args.input_sql:
        from src.execution import run_sql_file

        result = run_sql_file(args.input_sql, pipeline_config=config, result_suffix=args.result_suffix)
        print(f"script={result.script_path}")
        print(f"status={result.status}")
        print(f"result_dir={result.result_dir}")
        print(f"final_sql={result.artifacts['final_bigquery_sql']}")
        print(f"report_json={result.artifacts['run_report_json']}")
        if "run_trace_json" in result.artifacts:
            print(f"trace_json={result.artifacts['run_trace_json']}")
        print(f"log_txt={result.artifacts['run_log_txt']}")
        return 0
    if args.batch:
        from src.execution import run_sql_batch

        directory = args.input_dir or config.execution.default_input_dir
        results = run_sql_batch(directory, pipeline_config=config, result_suffix=args.result_suffix)
        print(f"batch_dir={directory}")
        print(f"scripts={len(results)}")
        for result in results:
            suffix = f" error={result.error}" if result.error else ""
            print(f"{result.script_path} status={result.status} result_dir={result.result_dir}{suffix}")
        return 0 if all(result.status == "validated" for result in results) else 1

    report = run(pipeline_config=config)
    print(f"status={report.status}")
    print(f"final_sql={report.artifacts['final_bigquery_sql']}")
    print(f"report_json={report.artifacts['run_report_json']}")
    if "run_trace_json" in report.artifacts:
        print(f"trace_json={report.artifacts['run_trace_json']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
