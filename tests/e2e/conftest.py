"""Session fixtures for the headless e2e regression suite.

`streamlit_app` force-restarts Streamlit against the current code on disk
(see `tests/_streamlit_lifecycle.py`) so the suite can never pass against a
stale process. `pytest-playwright` supplies the `page` fixture.

The webapp uses no TLS locally, so no `browser_context_args` override is
needed — unlike a self-signed-cert project, which would add
`ignore_https_errors` here.
"""

from __future__ import annotations

import os
from collections.abc import Iterator

import pytest

from tests._streamlit_lifecycle import (
    STREAMLIT_E2E_PORT,
    ensure_fresh_streamlit,
    kill_streamlit_on_port,
)

# Explicit bounded default for Playwright action + navigation waits (#61).
# Playwright's implicit 30 s stacks into opaque multi-minute hangs under
# pytest-timeout; 15 s fails fast with a TimeoutError that names the locator.
# Widen for slow CI runners via E2E_DEFAULT_TIMEOUT_MS without a code change.
_DEFAULT_TIMEOUT_MS = int(os.environ.get("E2E_DEFAULT_TIMEOUT_MS", "15000"))


@pytest.fixture(scope="session")
def streamlit_app() -> Iterator[str]:
    """Boot a fresh Streamlit for the whole pytest session; kill it after."""
    base_url = ensure_fresh_streamlit(STREAMLIT_E2E_PORT)
    try:
        yield base_url
    finally:
        kill_streamlit_on_port(STREAMLIT_E2E_PORT)


@pytest.fixture(autouse=True)
def _bound_default_timeouts(page) -> None:
    """Cap every Playwright action + navigation wait to _DEFAULT_TIMEOUT_MS (#61).

    Applied on the page (not context) because this scaffold's tests take the
    pytest-playwright page fixture directly — no custom context.new_page() path.
    """
    page.set_default_timeout(_DEFAULT_TIMEOUT_MS)
    page.set_default_navigation_timeout(_DEFAULT_TIMEOUT_MS)
