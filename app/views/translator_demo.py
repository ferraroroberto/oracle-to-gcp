"""Streamlit view for the Oracle-to-BigQuery mock translator."""

from __future__ import annotations

from dataclasses import asdict
from pathlib import Path

import streamlit as st

from src import clear_log_buffer, get_logger, stream_to_streamlit
from src.mock_environment import load_demo_script, load_mapping_registry
from src.pipeline_config import DEFAULT_PIPELINE_CONFIG_PATH, load_pipeline_config, with_overrides
from src.pipelines import oracle_to_bigquery

log = get_logger("ui.translator_demo")


def render() -> None:
    st.header("Oracle to BigQuery Translator Demo")

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

    script = st.text_area(
        "Oracle script",
        value=load_demo_script(),
        height=360,
        key="oracle_script",
    )

    col1, col2, col3, col4 = st.columns(4)
    with col1:
        use_local_hub = st.toggle(
            "Use local LLM hub",
            value=pipeline_config.run.use_local_hub,
            key="use_local_hub",
        )
    with col2:
        simulate_repair = st.toggle(
            "Simulate repair path",
            value=pipeline_config.run.simulate_repair_path,
            key="simulate_repair",
        )
    with col3:
        repair_limit = st.number_input(
            "Repair limit",
            min_value=0,
            max_value=5,
            value=pipeline_config.run.repair_limit,
            key="repair_limit",
        )
    with col4:
        trace_enabled = st.toggle(
            "Trace/debug",
            value=pipeline_config.trace.enabled,
            key="trace_enabled",
        )

    active_config = with_overrides(
        pipeline_config,
        use_local_hub=use_local_hub,
        simulate_repair_path=simulate_repair,
        repair_limit=int(repair_limit),
        trace_enabled=trace_enabled,
    )

    with st.expander("Mapping registry", expanded=False):
        st.json(load_mapping_registry())

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

    if st.button("Run mock translation", type="primary", key="run_translation"):
        clear_log_buffer()
        log.info("UI: starting Oracle to BigQuery mock translation")
        try:
            report = stream_to_streamlit(
                lambda: oracle_to_bigquery.run(
                    script=script,
                    pipeline_config=active_config,
                )
            )
        except Exception:
            log.exception("Translation run failed")
            st.error("Translation run failed. See the log panel above.")
            return

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

        with st.expander("Full report"):
            st.json(asdict(report))

        if report.trace:
            st.subheader("Trace/debug artifact")
            st.code(report.artifacts.get("run_trace_json", "(trace disabled)"), language="text")
            st.dataframe(
                [
                    {
                        "timestamp": event["timestamp"],
                        "stage": event["stage"],
                        "event": event["event"],
                    }
                    for event in report.trace
                ],
                width="stretch",
            )
            with st.expander("Trace event details"):
                st.json(report.trace)
