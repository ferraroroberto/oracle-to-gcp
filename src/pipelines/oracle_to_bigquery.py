"""End-to-end Oracle-to-BigQuery mock translation pipeline."""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from src import get_logger
from src.mock_environment import (
    bootstrap_mock_environment,
    connect_sqlite,
    load_demo_script,
    load_mapping_registry,
)
from src.sql_models import RunReport, SqlUnit
from src.sql_processing import build_units, materialize_variables
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
    use_local_hub: bool = True,
    repair_limit: int = 3,
    simulate_repair_path: bool = True,
    run_dir: Path | None = None,
) -> RunReport:
    """Run the full mock translation, execution, validation, and report flow."""
    paths = bootstrap_mock_environment(run_dir)
    mapping_registry = mapping or load_mapping_registry()
    source_script = script or load_demo_script()
    run_log: list[str] = []

    def record(message: str, *args: object) -> None:
        rendered = message % args if args else message
        run_log.append(rendered)
        log.info(rendered)

    record("Stage 0 ingest: mock Oracle=%s mock BigQuery=%s", paths["oracle_db"], paths["bigquery_db"])

    with connect_sqlite(paths["oracle_db"]) as oracle_conn, connect_sqlite(paths["bigquery_db"]) as bq_conn:
        pure_sql, resolved = materialize_variables(source_script, oracle_conn)
        record("Stage 1 materialized variables: %s", resolved)

        units = build_units(pure_sql)
        record("Stage 2 split script into %d ordered units", len(units))

        intermediate_mapping = _target_mapping(units)
        active_mapping = {**mapping_registry, **intermediate_mapping}
        _assert_all_sources_mapped(units, active_mapping)
        for unit in units:
            external_sources = [source for source in unit.sources if source in mapping_registry]
            checks = assert_rowcount_parity(oracle_conn, bq_conn, mapping_registry, external_sources)
            if checks:
                record("Stage 3 row-count parity for unit %d: %s", unit.id, checks)

        translator = TranslationEngine(
            use_local_hub=use_local_hub,
            simulate_first_attempt_mismatch=simulate_repair_path,
        )
        for unit in units:
            _translate_execute_validate(
                unit=unit,
                translator=translator,
                mapping=active_mapping,
                oracle_conn=oracle_conn,
                bq_conn=bq_conn,
                repair_limit=repair_limit,
                record=record,
            )

    final_script = "\n\n".join(unit.bq_sql.rstrip(";") + ";" for unit in units)
    timestamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    artifact_dir = Path(paths["run_dir"])
    script_path = artifact_dir / "final_bigquery.sql"
    report_path = artifact_dir / f"run_report_{timestamp}.json"
    script_path.write_text(final_script + "\n", encoding="utf-8")

    status = "validated" if all(unit.status == "validated" for unit in units) else "flagged"
    report = RunReport(
        script_id=f"demo-{timestamp}",
        status=status,
        resolved_variables=resolved,
        units=units,
        final_bigquery_script=final_script,
        artifacts={
            "final_bigquery_sql": str(script_path),
            "run_report_json": str(report_path),
            "oracle_mock_db": str(paths["oracle_db"]),
            "bigquery_mock_db": str(paths["bigquery_db"]),
        },
        log=run_log,
    )
    record("Stage 8 artifacts written: %s", artifact_dir)
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
) -> None:
    for attempt in range(repair_limit + 1):
        unit.bq_sql, unit.translator = translator.translate(unit.pure_oracle, mapping, attempt=attempt)
        if attempt == 0:
            record("Stage 4 translated unit %d via %s (%s)", unit.id, unit.translator, translator.last_note)
        else:
            record("Stage 7 repair attempt %d for unit %d via %s", attempt, unit.id, unit.translator)

        oracle_rows = execute_oracle_unit(oracle_conn, unit.pure_oracle)
        bq_rows = execute_bigquery_unit(bq_conn, unit.bq_sql)
        validation = compare_fingerprints(oracle_rows, bq_rows)
        unit.validation_result = validation

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


if __name__ == "__main__":
    run(use_local_hub=True)
