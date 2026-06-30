"""End-to-end tests for the mock Oracle-to-BigQuery pipeline."""

from __future__ import annotations

import json

from src.mock_environment import bootstrap_mock_environment, connect_sqlite, load_demo_script
from src.execution import find_previous_results, list_sql_scripts, load_report_json, run_sql_batch, run_sql_file
from src.pipeline_config import load_pipeline_config, with_overrides
from src.pipelines.oracle_to_bigquery import run
from src.pipelines.oracle_to_bigquery import main as pipeline_main
from src.sql_processing import build_units, materialize_variables


def test_materialize_variables_resolves_demo_run_date(tmp_path) -> None:
    paths = bootstrap_mock_environment(tmp_path)
    with connect_sqlite(paths["oracle_db"]) as conn:
        pure_sql, resolved = materialize_variables(load_demo_script(), conn)

    assert resolved == {"v_run_date": "DATE '2026-06-20'"}
    assert "v_run_date" not in pure_sql
    assert len(build_units(pure_sql)) == 2


def test_demo_pipeline_validates_and_repairs_with_mock_data(tmp_path) -> None:
    report = run(
        use_local_hub=False,
        simulate_repair_path=True,
        run_dir=tmp_path,
    )

    assert report.status == "validated"
    assert len(report.units) == 2
    assert report.units[0].repair_attempts == 1
    assert all(unit.status == "validated" for unit in report.units)
    assert "CREATE OR REPLACE TABLE scratch_daily_revenue AS" in report.final_bigquery_script
    assert "raw_sales_orders" in report.final_bigquery_script


def test_trace_artifact_captures_steps_queries_and_llm_payloads(tmp_path) -> None:
    config = with_overrides(
        load_pipeline_config(),
        use_local_hub=True,
        simulate_repair_path=True,
        output_dir=str(tmp_path),
        trace_enabled=True,
    )
    config.llm.base_url = "http://127.0.0.1:1"
    config.llm.timeout_seconds = 0.01

    report = run(pipeline_config=config)
    trace_path = report.artifacts["run_trace_json"]
    trace = json.loads(open(trace_path, encoding="utf-8").read())
    events = {(event["stage"], event["event"]) for event in trace["events"]}

    assert report.status == "validated"
    assert ("llm", "call_finished") in events
    assert ("execution", "oracle_unit_executed") in events
    assert ("execution", "bigquery_unit_executed") in events
    assert ("validation", "fingerprints_compared") in events
    assert any(event["details"].get("request_payload") for event in trace["events"] if event["stage"] == "llm")
    assert any(event["details"].get("rows") for event in trace["events"] if event["stage"] == "execution")


def test_cli_overrides_config_and_prints_trace_path(tmp_path, capsys) -> None:
    exit_code = pipeline_main(
        [
            "--output-dir",
            str(tmp_path),
            "--no-use-local-hub",
            "--repair-limit",
            "1",
            "--trace",
        ]
    )
    output = capsys.readouterr().out

    assert exit_code == 0
    assert "status=validated" in output
    assert "trace_json=" in output
    assert (tmp_path / "final_bigquery.sql").is_file()


def test_file_backed_execution_writes_sibling_bq_artifacts(tmp_path) -> None:
    script = tmp_path / "customer_revenue.sql"
    script.write_text(load_demo_script(), encoding="utf-8")
    config = with_overrides(
        load_pipeline_config(),
        use_local_hub=False,
        simulate_repair_path=True,
        trace_enabled=True,
    )

    result = run_sql_file(script, pipeline_config=config)

    assert result.status == "validated"
    assert result.result_dir == tmp_path / "customer_revenue_bq"
    assert (result.result_dir / "final_bigquery.sql").is_file()
    assert (result.result_dir / "source_oracle.sql").read_text(encoding="utf-8") == load_demo_script()
    assert result.artifacts["run_report_json"].endswith(".json")
    assert result.artifacts["run_trace_json"].endswith(".json")
    assert result.artifacts["run_log_txt"].endswith(".txt")

    reports = find_previous_results(tmp_path)
    assert len(reports) == 1
    loaded = load_report_json(reports[0])
    assert loaded["status"] == "validated"
    assert loaded["artifacts"]["input_sql"] == str(script)


def test_batch_execution_scans_only_sql_files(tmp_path) -> None:
    first = tmp_path / "first.sql"
    second = tmp_path / "second.SQL"
    first.write_text(load_demo_script(), encoding="utf-8")
    second.write_text(load_demo_script(), encoding="utf-8")
    (tmp_path / "notes.txt").write_text("not sql", encoding="utf-8")
    config = with_overrides(load_pipeline_config(), use_local_hub=False, simulate_repair_path=False)

    scripts = list_sql_scripts(tmp_path)
    results = run_sql_batch(tmp_path, pipeline_config=config)

    assert scripts == [first, second]
    assert len(results) == 2
    assert all(result.status == "validated" for result in results)
    assert len(find_previous_results(tmp_path)) == 2


def test_batch_execution_continues_after_script_failure(tmp_path) -> None:
    good = tmp_path / "good.sql"
    bad = tmp_path / "bad.sql"
    bad.write_text("SELECT * FROM missing_table;", encoding="utf-8")
    good.write_text(load_demo_script(), encoding="utf-8")
    config = with_overrides(load_pipeline_config(), use_local_hub=False, simulate_repair_path=False)

    results = run_sql_batch(tmp_path, pipeline_config=config)

    assert [result.script_path.name for result in results] == ["bad.sql", "good.sql"]
    assert results[0].status == "failed"
    assert "unmapped source table" in results[0].error
    assert results[1].status == "validated"


def test_cli_single_file_and_batch_execution(tmp_path, capsys) -> None:
    script = tmp_path / "single.sql"
    script.write_text(load_demo_script(), encoding="utf-8")
    single_exit = pipeline_main(["--input-sql", str(script), "--no-use-local-hub", "--trace"])
    single_output = capsys.readouterr().out

    assert single_exit == 0
    assert "result_dir=" in single_output
    assert (tmp_path / "single_bq" / "final_bigquery.sql").is_file()

    batch_exit = pipeline_main(["--batch", "--input-dir", str(tmp_path), "--no-use-local-hub"])
    batch_output = capsys.readouterr().out

    assert batch_exit == 0
    assert "scripts=1" in batch_output
