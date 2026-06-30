"""Overview view."""

import streamlit as st

from src.config import APP_NAME


def render() -> None:
    st.title(APP_NAME)
    st.markdown(
        """
        Local Streamlit prototype for translating Oracle SQL scripts into
        BigQuery Standard SQL through a deterministic, auditable pipeline.

        The demo uses SQLite files as mock Oracle and BigQuery engines. It
        materializes a simple PL/SQL variable, splits the pure SQL into ordered
        units, checks source table row-count parity, calls the local LLM hub as
        a stateless translation function when available, executes both sides,
        compares fingerprints, and emits the final BigQuery script plus a JSON
        run report.

        Open **Translator Demo** to run the end-to-end mock.
        """
    )
