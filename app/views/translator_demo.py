"""Streamlit view for traceable Oracle-to-BigQuery execution."""

from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path
from typing import Any

import streamlit as st

from src import clear_log_buffer, get_logger, stream_to_streamlit
from src.execution import (
    ensure_execution_input_dir,
    find_previous_results,
    list_sql_scripts,
    load_report_json,
    run_sql_batch,
    run_sql_file,
)
from src.mock_environment import load_demo_script, load_mapping_registry
from src.pipeline_config import (
    DEFAULT_PIPELINE_CONFIG_PATH,
    config_to_dict,
    load_pipeline_config,
    with_overrides,
    write_pipeline_config,
)
from src.pipelines import oracle_to_bigquery
from src.sql_models import RunReport

log = get_logger("ui.execution")


def render() -> None:
    st.header("Oracle to BigQuery Execution")

    config_path = st.text_input(
        "Pipeline config JSON",
        value=str(DEFAULT_PIPELINE_CONFIG_PATH),
        key="pipeline_config_path",
    )
    try:
        pipeline_config = load_pipeline_config(Path(config_path))
    except Exception as exc:
        st.error(f"Could not load pipeline config: {exc}")
        return

    demo_tab, execution_tab, config_tab = st.tabs(["Demo", "Execution", "Configuration"])
    with demo_tab:
        _render_demo_tab(pipeline_config)
    with execution_tab:
        _render_execution_tab(pipeline_config)
    with config_tab:
        _render_configuration_tab(Path(config_path), pipeline_config)


def _render_demo_tab(pipeline_config) -> None:
    script = st.text_area(
        "Oracle script",
        value=load_demo_script(),
        height=320,
        key="demo_oracle_script",
    )
    active_config = _render_run_controls("demo", pipeline_config)

    with st.expander("Mapping registry", expanded=False):
        st.json(load_mapping_registry())
    _render_active_config(active_config)

    if st.button("Run demo translation", type="primary", key="demo_run_translation"):
        clear_log_buffer()
        log.info("UI: starting demo Oracle to BigQuery translation")
        try:
            report = stream_to_streamlit(
                lambda: oracle_to_bigquery.run(script=script, pipeline_config=active_config)
            )
        except Exception:
            log.exception("Demo translation run failed")
            st.error("Translation run failed. See the log panel above.")
            return
        _render_report(report)


def _render_execution_tab(pipeline_config) -> None:
    ensure_execution_input_dir(pipeline_config)
    active_config = _render_run_controls("execution", pipeline_config)

    mode = st.radio(
        "Execution mode",
        ["Single file", "Batch directory"],
        horizontal=True,
        key="execution_mode",
    )
    default_dir = str(ensure_execution_input_dir(active_config))
    if mode == "Single file":
        sql_path = st.text_input("SQL file path", value="", key="execution_sql_file")
        if st.button("Run selected SQL", type="primary", key="execution_run_single"):
            _run_single_file(sql_path, active_config)
    else:
        input_dir = st.text_input("SQL directory", value=default_dir, key="execution_sql_dir")
        try:
            scripts = list_sql_scripts(input_dir)
        except Exception as exc:
            st.error(f"Could not scan directory: {exc}")
            scripts = []
        st.caption(f"{len(scripts)} SQL file(s) found")
        if scripts:
            st.dataframe([{"script": str(path)} for path in scripts], width="stretch")
        if st.button("Run batch", type="primary", key="execution_run_batch"):
            _run_batch(input_dir, active_config)

    st.divider()
    _render_previous_results(active_config)


def _render_configuration_tab(config_path: Path, pipeline_config) -> None:
    st.caption("Edit the JSON config saved on disk. Invalid JSON is rejected before writing.")
    raw_json = st.text_area(
        "Config JSON",
        value=json.dumps(config_to_dict(pipeline_config), indent=2),
        height=520,
        key="config_json_editor",
    )
    if st.button("Save configuration", type="primary", key="save_pipeline_config"):
        try:
            raw = json.loads(raw_json)
            saved = write_pipeline_config(config_path, raw)
        except Exception as exc:
            st.error(f"Could not save config: {exc}")
            return
        st.success(f"Saved config to {saved.path}")
        st.json(config_to_dict(saved))


def _render_run_controls(prefix: str, pipeline_config):
    col1, col2, col3, col4 = st.columns(4)
    with col1:
        use_local_hub = st.toggle(
            "Use local LLM hub",
            value=pipeline_config.run.use_local_hub,
            key=f"{prefix}_use_local_hub",
        )
    with col2:
        simulate_repair = st.toggle(
            "Simulate repair path",
            value=pipeline_config.run.simulate_repair_path,
            key=f"{prefix}_simulate_repair",
        )
    with col3:
        repair_limit = st.number_input(
            "Repair limit",
            min_value=0,
            max_value=5,
            value=pipeline_config.run.repair_limit,
            key=f"{prefix}_repair_limit",
        )
    with col4:
        trace_enabled = st.toggle(
            "Trace/debug",
            value=pipeline_config.trace.enabled,
            key=f"{prefix}_trace_enabled",
        )
    return with_overrides(
        pipeline_config,
        use_local_hub=use_local_hub,
        simulate_repair_path=simulate_repair,
        repair_limit=int(repair_limit),
        trace_enabled=trace_enabled,
    )


def _render_active_config(active_config) -> None:
    with st.expander("Active LLM prompt and parameters", expanded=False):
        st.json(
            {
                "base_url": active_config.llm.base_url,
                "model": active_config.llm.model,
                "timeout_seconds": active_config.llm.timeout_seconds,
                "temperature": active_config.llm.temperature,
                "extra_parameters": active_config.llm.extra_parameters,
            }
        )
        st.markdown("**System message**")
        st.code(active_config.llm.system_message, language="text")
        st.markdown("**User prompt template**")
        st.code(active_config.llm.user_prompt_template, language="text")
    with st.expander("Active pipeline config", expanded=False):
        st.json(asdict(active_config))


def _run_single_file(sql_path: str, active_config) -> None:
    clear_log_buffer()
    log.info("UI: starting file-backed SQL execution")
    try:
        result = stream_to_streamlit(lambda: run_sql_file(sql_path, pipeline_config=active_config))
    except Exception:
        log.exception("File-backed execution failed")
        st.error("Execution failed. See the log panel above.")
        return
    st.success(f"{result.script_path.name}: {result.status}")
    st.code(str(result.result_dir), language="text")
    _render_loaded_report(load_report_json(result.artifacts["run_report_json"]))


def _run_batch(input_dir: str, active_config) -> None:
    clear_log_buffer()
    log.info("UI: starting batch SQL execution")
    try:
        results = stream_to_streamlit(lambda: run_sql_batch(input_dir, pipeline_config=active_config))
    except Exception:
        log.exception("Batch execution failed")
        st.error("Batch execution failed. See the log panel above.")
        return
    st.success(f"Batch complete: {len(results)} script(s)")
    st.dataframe(
        [
            {
                "script": str(result.script_path),
                "status": result.status,
                "error": result.error,
                "result_dir": str(result.result_dir),
                "report": result.artifacts.get("run_report_json", ""),
            }
            for result in results
        ],
        width="stretch",
    )


def _render_previous_results(active_config) -> None:
    result_root = st.text_input(
        "Previous result search directory",
        value=str(ensure_execution_input_dir(active_config)),
        key="previous_result_root",
    )
    reports = find_previous_results(result_root, result_suffix=active_config.execution.result_suffix)
    st.caption(f"{len(reports)} previous report(s) found")
    if not reports:
        return
    labels = [f"{path.parent.name} / {path.name}" for path in reports]
    selected = st.selectbox("Previous result", labels, key="previous_result_select")
    selected_path = reports[labels.index(selected)]
    col1, col2 = st.columns(2)
    with col1:
        if st.button("Load selected result", key="load_selected_result"):
            st.session_state["loaded_execution_report"] = str(selected_path)
    with col2:
        if st.button("Load latest result", key="load_latest_result"):
            st.session_state["loaded_execution_report"] = str(reports[0])

    loaded = st.session_state.get("loaded_execution_report")
    if loaded:
        _render_loaded_report(load_report_json(loaded))


def _render_report(report: RunReport) -> None:
    st.success(f"Run status: {report.status}")
    st.subheader("Resolved variables")
    st.json(report.resolved_variables)

    st.subheader("Unit status")
    st.dataframe(
        [
            {
                "id": unit.id,
                "type": unit.statement_type,
                "sources": ", ".join(unit.sources),
                "targets": ", ".join(unit.targets),
                "status": unit.status,
                "repairs": unit.repair_attempts,
                "translator": unit.translator,
            }
            for unit in report.units
        ],
        width="stretch",
    )

    st.subheader("Final BigQuery script")
    st.code(report.final_bigquery_script, language="sql")
    _render_artifacts(report.artifacts, report.trace)
    with st.expander("Full report"):
        st.json(asdict(report))


def _render_loaded_report(report: dict[str, Any]) -> None:
    st.subheader("Loaded result")
    st.success(f"Run status: {report.get('status', 'unknown')}")
    st.subheader("Final BigQuery script")
    st.code(str(report.get("final_bigquery_script", "")), language="sql")
    _render_artifacts(report.get("artifacts", {}), report.get("trace", []))
    with st.expander("Full loaded report"):
        st.json(report)


def _render_artifacts(artifacts: dict[str, str], trace: list[dict[str, Any]]) -> None:
    st.subheader("Artifacts")
    st.json(artifacts)
    if trace:
        st.subheader("Trace/debug artifact")
        st.code(artifacts.get("run_trace_json", "(trace disabled)"), language="text")
        st.dataframe(
            [
                {
                    "timestamp": event["timestamp"],
                    "stage": event["stage"],
                    "event": event["event"],
                }
                for event in trace
            ],
            width="stretch",
        )
        with st.expander("Trace event details"):
            st.json(trace)
