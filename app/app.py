"""
Streamlit entry point
=====================
Run with:

    streamlit run app/app.py

Responsibility split:
* This file owns ``st.set_page_config``, the **sidebar** (native
  navigation built with ``st.navigation`` + ``st.Page``, plus a
  light/dark toggle), and dispatch to the selected view.
* Each view in ``app/views/`` exposes a ``render()`` callable.
* All business logic lives in ``src/``.

The directory is named ``views`` (not ``pages``) on purpose: Streamlit
auto-discovers any subdirectory called ``pages/`` and adds it to the
sidebar, which would duplicate the navigation built here.

Theming:
* Default (dark) theme — ``app/.streamlit/config.toml``.  That is
  Streamlit's native theme mechanism: it sets CSS variables that
  propagate to *every* component (including charts, internal widgets,
  and states not exposed via ``data-testid``).
* Light-mode overlay — ``app/styles/light.css``.  Injected at runtime
  when the sidebar toggle is on.  Can't be expressed in config.toml
  because config is read once at startup.  See README for details.
"""

import sys
from pathlib import Path

# Ensure project root is on sys.path so ``src.*`` imports resolve.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import streamlit as st

from src.config import APP_NAME
from src.logger import get_logger
from views import translator_demo, welcome

# ---------------------------------------------------------------------------
# Page config (must be first Streamlit call)
# ---------------------------------------------------------------------------
st.set_page_config(
    page_title=APP_NAME,
    page_icon="BQ",
    layout="wide",
    initial_sidebar_state="expanded",
)

get_logger("app").debug("Streamlit session started")

# ---------------------------------------------------------------------------
# Theming helpers
# ---------------------------------------------------------------------------
_STYLES_DIR = Path(__file__).resolve().parent / "styles"


def _inject_css(filename: str) -> None:
    """Read a stylesheet from app/styles/ and inject it into the page."""
    css = (_STYLES_DIR / filename).read_text(encoding="utf-8")
    st.markdown(f"<style>{css}</style>", unsafe_allow_html=True)


# ---------------------------------------------------------------------------
# Sidebar — light/dark toggle (rendered *below* the auto-generated nav).
# Add new views by appending to ``nav_pages`` further down.
# ---------------------------------------------------------------------------
with st.sidebar:
    if st.toggle("☀  Light mode", value=False, key="light_mode"):
        _inject_css("light.css")

# ---------------------------------------------------------------------------
# Navigation — native st.navigation (no extra widgets, no title)
# ---------------------------------------------------------------------------
nav_pages = [
    st.Page(welcome.render, title="Overview", icon=":material/database:", url_path="welcome", default=True),
    st.Page(
        translator_demo.render,
        title="Execution",
        icon=":material/transform:",
        url_path="execution",
    ),
]

pg = st.navigation(nav_pages, position="sidebar")
pg.run()
