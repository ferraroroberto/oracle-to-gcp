"""Executive process map view."""

from base64 import b64encode
from pathlib import Path

import streamlit as st


_DOCS_DIR = Path(__file__).resolve().parents[2] / "docs"
_PROCESS_MAP_HTML = _DOCS_DIR / "oracle-to-gcp-process-map.html"
_PROCESS_MAP_SNAPSHOT = _DOCS_DIR / "oracle-to-gcp-process-map.png"


def render() -> None:
    """Render the static Oracle-to-GCP process map inside Streamlit."""
    st.title("Process Map")
    st.caption(
        "Executive one-page map of the Oracle-to-GCP migration workflow, technology stack, value, and tradeoffs."
    )

    html = _PROCESS_MAP_HTML.read_text(encoding="utf-8")
    initial_theme = "light" if st.session_state.get("light_mode") else "dark"
    html = html.replace('<html lang="en" data-theme="dark">', f'<html lang="en" data-theme="{initial_theme}">')

    encoded_html = b64encode(html.encode("utf-8")).decode("ascii")
    st.iframe(f"data:text/html;base64,{encoded_html}", height=560)

    with st.expander("Static artifacts", expanded=False):
        st.markdown(f"- HTML one-pager: `{_PROCESS_MAP_HTML.as_posix()}`")
        if _PROCESS_MAP_SNAPSHOT.exists():
            st.markdown(f"- PNG snapshot: `{_PROCESS_MAP_SNAPSHOT.as_posix()}`")
            st.image(str(_PROCESS_MAP_SNAPSHOT), caption="Process map snapshot", width="stretch")
        else:
            st.warning("PNG snapshot has not been generated yet.")
