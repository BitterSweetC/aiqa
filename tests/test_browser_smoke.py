"""Deterministic local browser smoke test used by CI."""

from __future__ import annotations

import pytest
from playwright.sync_api import sync_playwright


@pytest.mark.browser
def test_chromium_can_execute_action_and_observe_effect() -> None:
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        try:
            page = browser.new_page()
            page.set_content(
                """
                <!doctype html>
                <button id="checkout" onclick="this.textContent='Order placed'">
                  Place order
                </button>
                """
            )

            page.locator("#checkout").click()

            assert page.locator("#checkout").text_content() == "Order placed"
        finally:
            browser.close()
