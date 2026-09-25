"""Unit tests for AIQA SiteInspector and TestPlanner (Phase 2 MVP-2)."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from aiqa.models.test_case import TestSuite
from aiqa.planner.site_inspector import InspectedPage, SiteInspector
from aiqa.planner.test_generator import TestPlanner


def test_inspected_page_to_prompt_context():
    """Verify InspectedPage formats prompt context accurately."""
    page = InspectedPage(
        url="https://shop.example.com",
        title="Modern E-Commerce Store",
        headings=["Featured Products", "Top Categories"],
        search_inputs=[
            {"selector": "#site-search", "name": "q", "placeholder": "Search items..."}
        ],
        buttons=[
            {"text": "Add to Cart", "selector": ".add-to-cart", "tag": "button"},
            {"text": "Checkout", "selector": "#checkout", "tag": "button"},
        ],
        forms=[
            {
                "action": "/search",
                "method": "GET",
                "inputs": [{"name": "q", "type": "search"}],
            }
        ],
        nav_links=[
            {"text": "Home", "href": "https://shop.example.com/"},
            {"text": "Electronics", "href": "https://shop.example.com/electronics"},
        ],
    )

    context = page.to_prompt_context()
    assert "https://shop.example.com" in context
    assert "Modern E-Commerce Store" in context
    assert "Featured Products" in context
    assert "#site-search" in context
    assert "Add to Cart" in context
    assert "Electronics" in context


def test_test_planner_heuristic_generation(tmp_path: Path):
    """TestPlanner generates valid TestSuite rule-based when no LLM key is provided."""
    page = InspectedPage(
        url="https://demo.store.com",
        title="Demo Store Home",
        headings=["Welcome to Demo Store"],
        search_inputs=[
            {"selector": "#search-bar", "name": "search", "placeholder": "Search products"}
        ],
        buttons=[
            {"text": "Subscribe", "selector": "#btn-sub", "tag": "button"}
        ],
        nav_links=[
            {"text": "Catalog", "href": "https://demo.store.com/catalog"},
            {"text": "About", "href": "https://demo.store.com/about"},
        ],
    )

    planner = TestPlanner()
    suite = planner.generate_suite(page, goal="Focus on product search and catalog navigation", max_tests=4)

    assert isinstance(suite, TestSuite)
    assert suite.base_url == "https://demo.store.com"
    assert len(suite.tests) >= 2
    assert len(suite.tests) <= 4

    test_ids = [t.id for t in suite.tests]
    assert "SMOKE-001" in test_ids
    assert "SEARCH-001" in test_ids
    assert all(test.actions for test in suite.tests)
    assert "ACTION-001" not in test_ids
    search_test = next(test for test in suite.tests if test.id == "SEARCH-001")
    search_value = next(action.value for action in search_test.actions if action.action == "fill")
    assert any(
        exp.type == "url" and exp.value == search_value for exp in search_test.expected
    )
    nav_tests = [test for test in suite.tests if test.id.startswith("NAV-")]
    assert all(test.actions[0].action == "click" for test in nav_tests)
    assert all(test.expected[0].type == "url" for test in nav_tests)

    # Save to disk
    out_file = tmp_path / "planned_suite.json"
    saved = planner.save_suite(suite, out_file)
    assert saved.exists()

    # Verify JSON content is valid
    data = json.loads(saved.read_text(encoding="utf-8"))
    loaded_suite = TestSuite.model_validate(data)
    assert loaded_suite.name == suite.name
    assert len(loaded_suite.tests) == len(suite.tests)


def test_test_planner_llm_generation_mocked():
    """TestPlanner parses LLM response and validates with Pydantic."""
    page = InspectedPage(
        url="https://demo.store.com",
        title="Demo Store Home",
        headings=["Store"],
        search_inputs=[],
        buttons=[],
        nav_links=[],
    )

    mock_llm_response = {
        "name": "AI Generated Smoke Suite",
        "base_url": "https://demo.store.com",
        "tests": [
            {
                "id": "AI-SMOKE-001",
                "name": "Verify Store Title",
                "start_url": "https://demo.store.com",
                "preconditions": [],
                "goal": "Go to homepage and check title",
                "actions": [
                    {
                        "action": "navigate",
                        "url": "https://demo.store.com",
                        "description": "Open the store",
                    }
                ],
                "expected": [
                    {
                        "type": "dom",
                        "description": "Title contains Demo Store",
                        "selector": "title",
                        "value": "Demo Store",
                    }
                ],
                "cleanup": [],
                "tags": ["smoke"],
                "timeout": 30,
            }
        ],
    }

    mock_completion = MagicMock()
    mock_choice = MagicMock()
    mock_choice.message.content = f"```json\n{json.dumps(mock_llm_response)}\n```"
    mock_completion.choices = [mock_choice]

    planner = TestPlanner(api_key="sk-test-key-mock")

    with patch("openai.OpenAI") as mock_openai_cls:
        mock_client = MagicMock()
        mock_client.chat.completions.create.return_value = mock_completion
        mock_openai_cls.return_value = mock_client

        suite = planner.generate_suite(page, goal="Smoke test homepage")
        assert isinstance(suite, TestSuite)
        assert suite.name == "AI Generated Smoke Suite"
        assert len(suite.tests) == 1
        assert suite.tests[0].id == "AI-SMOKE-001"
        assert suite.tests[0].expected[0].type == "dom"


def test_heuristic_planner_omits_button_without_observable_effect():
    """A button with no known outcome does not become a false-positive interaction test."""
    page = InspectedPage(
        url="https://demo.store.com",
        title="Demo Store",
        headings=[],
        search_inputs=[],
        buttons=[{"text": "Subscribe", "selector": "#subscribe", "tag": "button"}],
        nav_links=[],
    )

    suite = TestPlanner().generate_suite(page, max_tests=5)

    assert [test.id for test in suite.tests] == ["SMOKE-001"]
    assert suite.tests[0].actions


@pytest.mark.asyncio
async def test_site_inspector_inspect_page(tmp_path: Path):
    """SiteInspector extracts elements from an active BrowserSession."""
    from aiqa.executor.browser_session import BrowserSession

    html_content = """<!DOCTYPE html>
    <html>
      <head><title>Test Store</title></head>
      <body>
        <h1>Featured Electronics</h1>
        <form action="/search" method="get">
          <input type="search" id="search-box" name="q" placeholder="Search gadgets...">
          <button type="submit" id="search-btn">Search</button>
        </form>
        <nav>
          <a href="/deals">Hot Deals</a>
          <a href="/cart">Cart (0)</a>
        </nav>
      </body>
    </html>
    """

    async with BrowserSession(headless=True) as session:
        await session.page.set_content(html_content)
        inspector = SiteInspector()
        inspected = await inspector.inspect_page(session)

    assert inspected.title == "Test Store"
    assert "Featured Electronics" in inspected.headings
    assert len(inspected.search_inputs) >= 1
    assert inspected.search_inputs[0]["selector"] == "#search-box"
    assert any("Search" in b.get("text", "") for b in inspected.buttons)
    assert any("Hot Deals" in l.get("text", "") for l in inspected.nav_links)
