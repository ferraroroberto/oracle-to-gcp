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
    page.goto(f"{streamlit_app}/execution")
    page.wait_for_selector('[data-testid="stAppViewContainer"]', timeout=20_000)

    expect(page.get_by_role("heading", name="Oracle to BigQuery Execution")).to_be_visible()
    expect(page.get_by_label("Pipeline config JSON")).to_be_visible()
    expect(page.get_by_role("tab", name="Demo")).to_be_visible()
    expect(page.get_by_role("tab", name="Execution")).to_be_visible()
    expect(page.get_by_role("tab", name="Connection Configuration")).to_be_visible()
    expect(page.get_by_role("tab", name="Table Correspondence")).to_be_visible()
    expect(page.get_by_role("tab", name="Advanced JSON")).to_be_visible()
    page.get_by_text("Active LLM prompt and parameters").click()
    expect(page.get_by_text('"claude-haiku-4-5"').first).to_be_visible()
    expect(page.get_by_text("User prompt template")).to_be_visible()


def test_execution_and_configuration_tabs_render(streamlit_app: str, page: Page) -> None:
    page.goto(f"{streamlit_app}/execution")
    page.wait_for_selector('[data-testid="stAppViewContainer"]', timeout=20_000)

    page.get_by_role("tab", name="Execution").click()
    expect(page.get_by_text("Execution mode")).to_be_visible()
    expect(page.get_by_label("SQL file path")).to_be_visible()
    page.get_by_text("Batch directory").click()
    expect(page.get_by_label("SQL directory")).to_be_visible()
    expect(page.get_by_text("Previous result search directory")).to_be_visible()

    page.get_by_role("tab", name="Connection Configuration").click()
    expect(page.get_by_label("LLM base URL")).to_be_visible()
    expect(page.get_by_role("button", name="Test configured connections")).to_be_visible()

    page.get_by_role("tab", name="Table Correspondence").click()
    expect(page.get_by_role("button", name="Download CSV template")).to_be_visible()

    page.get_by_role("tab", name="Advanced JSON").click()
    expect(page.get_by_role("textbox", name="Config JSON", exact=True)).to_be_visible()
    expect(page.get_by_role("button", name="Save configuration")).to_be_visible()
