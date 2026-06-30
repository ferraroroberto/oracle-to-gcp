"""Example e2e smoke test — proof the headless harness works.

This is the *one* test a fresh scaffold ships with. Expand per the
regression-suite rules in CLAUDE.md ("End-to-end UI testing"): add a test
only when silent breakage would hurt, a unit test can't catch it, and the
behaviour has stabilised. Don't grow this file into a full UI net.
"""

from __future__ import annotations

from playwright.sync_api import Page, expect


def test_app_boots_and_renders_welcome(streamlit_app: str, page: Page) -> None:
    page.goto(streamlit_app)
    # Streamlit hydrates client-side; wait for the app shell, then assert the
    # default Welcome view rendered its title (app/views/welcome.py).
    page.wait_for_selector('[data-testid="stAppViewContainer"]', timeout=20_000)
    expect(page.get_by_role("heading", name="Oracle to GCP")).to_be_visible(timeout=10_000)


def test_translator_demo_shows_config_and_prompt(streamlit_app: str, page: Page) -> None:
    page.goto(f"{streamlit_app}/translator-demo")
    page.wait_for_selector('[data-testid="stAppViewContainer"]', timeout=20_000)

    expect(page.get_by_role("heading", name="Oracle to BigQuery Translator Demo")).to_be_visible()
    expect(page.get_by_label("Pipeline config JSON")).to_be_visible()
    page.get_by_text("Active LLM prompt and parameters").click()
    expect(page.get_by_text('"claude-haiku-4-5"').first).to_be_visible()
    expect(page.get_by_text("User prompt template")).to_be_visible()
