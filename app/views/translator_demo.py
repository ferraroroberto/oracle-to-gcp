"""Streamlit view for the Oracle-to-BigQuery mock translator."""

from __future__ import annotations

from dataclasses import asdict

import streamlit as st

from src import clear_log_buffer, get_logger, stream_to_streamlit
from src.mock_environment import load_demo_script, load_mapping_registry
from src.pipelines import oracle_to_bigquery

log = get_logger("ui.translator_demo")


def render() -> None:
    st.header("Oracle to BigQuery Translator Demo")

    script = st.text_area(
        "Oracle script",
        value=load_demo_script(),
        height=360,
        key="oracle_script",
    )

    col1, col2, col3 = st.columns(3)
    with col1:
        use_local_hub = st.toggle("Use local LLM hub", value=True, key="use_local_hub")
    with col2:
        simulate_repair = st.toggle("Simulate repair path", value=True, key="simulate_repair")
    with col3:
        repair_limit = st.number_input("Repair limit", min_value=0, max_value=5, value=3, key="repair_limit")

    with st.expander("Mapping registry", expanded=False):
        st.json(load_mapping_registry())

    if st.button("Run mock translation", type="primary", key="run_translation"):
        clear_log_buffer()
        log.info("UI: starting Oracle to BigQuery mock translation")
        try:
            report = stream_to_streamlit(
                lambda: oracle_to_bigquery.run(
                    script=script,
                    use_local_hub=use_local_hub,
                    repair_limit=int(repair_limit),
                    simulate_repair_path=simulate_repair,
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
