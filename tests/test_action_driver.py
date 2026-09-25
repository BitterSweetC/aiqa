"""Unit tests for ActionDriver (Stage 4 Autonomous Execution Engine)."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from pydantic import TypeAdapter, ValidationError

from aiqa.executor.action_driver import ActionDriver
from aiqa.executor.browser_session import BrowserSession
from aiqa.models.test_case import BrowserAction, TestCase


def test_typed_action_contract_validates_required_fields_and_bounds():
    """Executable actions reject incomplete and unbounded instructions."""
    adapter = TypeAdapter(BrowserAction)

    assert adapter.validate_python({"action": "click", "selector": "#save"}).selector == "#save"
    assert adapter.validate_python({"action": "wait", "timeout_ms": 250}).timeout_ms == 250

    with pytest.raises(ValidationError):
        adapter.validate_python({"action": "fill", "selector": "#name"})
    with pytest.raises(ValidationError):
        adapter.validate_python({"action": "wait", "timeout_ms": 60_000})
    with pytest.raises(ValidationError):
        adapter.validate_python({"action": "scroll", "selector": "body"})
    with pytest.raises(ValidationError):
        adapter.validate_python(
            {"action": "click", "selector": "#save", "seletor": "#typo"}
        )
    with pytest.raises(ValidationError):
        adapter.validate_python({"action": "click", "selector": "   "})


def test_existing_goal_only_suite_remains_loadable_for_migration():
    """Legacy suites can still load while actions are introduced incrementally."""
    test = TestCase(
        id="LEGACY-001",
        name="Legacy suite",
        start_url="about:blank",
        goal="Click the 'Save' button",
        expected=[],
    )

    assert test.actions is None


@pytest.mark.asyncio
async def test_action_driver_rejects_unsupported_goal():
    """An unrecognized natural-language goal is a structured failure."""
    driver = ActionDriver()

    steps = await driver.execute_goal("Inspect this page somehow", AsyncMock())

    assert steps[-1]["status"] == "failed"
    assert steps[-1]["error"]["code"] == "unsupported_goal"


@pytest.mark.asyncio
async def test_action_driver_stops_typed_plan_after_first_required_failure():
    """A required action failure prevents later actions from executing."""
    page = AsyncMock()
    missing = AsyncMock()
    missing.first = missing
    missing.click.side_effect = RuntimeError("target missing")
    later = AsyncMock()
    later.first = later
    page.locator = MagicMock(side_effect=[missing, later])
    driver = ActionDriver()

    steps = await driver.execute_goal(
        "Ignored when typed actions are present",
        page,
        actions=[
            {"action": "click", "selector": "#missing"},
            {"action": "fill", "selector": "#later", "value": "must not run"},
        ],
    )

    assert len(steps) == 1
    assert steps[0]["status"] == "failed"
    assert steps[0]["error"]["code"] == "action_execution_failed"
    later.fill.assert_not_awaited()


@pytest.mark.asyncio
async def test_action_driver_rejects_plan_with_only_optional_actions():
    """A plan must contain an action whose success is required for the test."""
    driver = ActionDriver()

    steps = await driver.execute_goal(
        "Optional probe",
        AsyncMock(),
        actions=[{"action": "wait", "timeout_ms": 10, "required": False}],
    )

    assert steps[-1]["status"] == "failed"
    assert steps[-1]["error"]["code"] == "missing_required_action"


@pytest.mark.asyncio
async def test_action_driver_rejects_unknown_llm_action():
    """An LLM proposal outside the action contract cannot be recorded as completed."""
    mock_completion = MagicMock()
    mock_choice = MagicMock()
    mock_choice.message.content = '[{"action": "delete_all", "description": "unsafe"}]'
    mock_completion.choices = [mock_choice]
    page = AsyncMock()
    page.url = "https://example.test"
    page.title.return_value = "Example"
    page.evaluate.return_value = ""

    driver = ActionDriver(api_key="sk-test-mock-key")
    with patch("openai.OpenAI") as mock_openai_cls:
        client = MagicMock()
        client.chat.completions.create.return_value = mock_completion
        mock_openai_cls.return_value = client
        steps = await driver.execute_goal("Delete everything", page)

    assert steps[-1]["status"] == "failed"
    assert steps[-1]["error"]["code"] == "invalid_action_plan"


@pytest.mark.asyncio
async def test_action_driver_heuristic_link_click():
    """ActionDriver accurately clicks navigation link based on goal description."""
    html_page = """<!DOCTYPE html>
    <html>
      <head><title>Test Page</title></head>
      <body>
        <a id="more-link" href="#target-section">Learn more</a>
        <div id="target-section">Content</div>
      </body>
    </html>
    """

    async with BrowserSession(headless=True) as session:
        await session.page.set_content(html_page)
        driver = ActionDriver()

        goal = "Click the navigation link with text 'Learn more'"
        steps = await driver.execute_goal(goal, session.page)

        assert len(steps) >= 1
        assert any(s["action"] == "click" and s["status"] == "completed" for s in steps)


@pytest.mark.asyncio
async def test_action_driver_heuristic_search_fill():
    """ActionDriver fills search input and triggers Enter."""
    html_page = """<!DOCTYPE html>
    <html>
      <body>
        <input type="search" id="search-box" name="q" placeholder="Search products...">
      </body>
    </html>
    """

    async with BrowserSession(headless=True) as session:
        await session.page.set_content(html_page)
        driver = ActionDriver()

        goal = "Locate search input using selector '#search-box', type 'keyboard', and submit"
        steps = await driver.execute_goal(goal, session.page)

        val = await session.page.locator("#search-box").input_value()
        assert val == "keyboard"
        assert any(s["action"] == "fill" for s in steps)


@pytest.mark.asyncio
async def test_action_driver_heuristic_button_click():
    """ActionDriver clicks button specified in goal."""
    html_page = """<!DOCTYPE html>
    <html>
      <body>
        <button id="cart-btn" onclick="this.innerText='Added'">Add to Cart</button>
      </body>
    </html>
    """

    async with BrowserSession(headless=True) as session:
        await session.page.set_content(html_page)
        driver = ActionDriver()

        goal = "Click the 'Add to Cart' button"
        steps = await driver.execute_goal(goal, session.page)

        text = await session.page.locator("#cart-btn").inner_text()
        assert text == "Added"
        assert any(s["action"] == "click" for s in steps)


@pytest.mark.asyncio
async def test_action_driver_typed_missing_target_fails_in_browser():
    """A missing typed target produces a structured browser execution failure."""
    async with BrowserSession(headless=True) as session:
        await session.page.set_content("<html><body><p>No button here</p></body></html>")
        steps = await ActionDriver().execute_goal(
            "Click missing target",
            session.page,
            actions=[{"action": "click", "selector": "#missing"}],
        )

    assert steps[-1]["status"] == "failed"
    assert steps[-1]["error"]["code"] == "action_execution_failed"


@pytest.mark.asyncio
async def test_action_driver_llm_execution_mocked():
    """ActionDriver executes actions generated by LLM when API key is provided."""
    mock_actions = [
        {"action": "fill", "selector": "#username", "value": "alice", "description": "Type username"},
        {"action": "click", "selector": "#login-btn", "description": "Click Login"},
    ]

    mock_completion = MagicMock()
    mock_choice = MagicMock()
    mock_choice.message.content = f"```json\n{json.dumps(mock_actions)}\n```"
    mock_completion.choices = [mock_choice]

    mock_page = AsyncMock()
    mock_page.url = "https://example.com/login"
    mock_page.title.return_value = "Login Page"
    mock_page.evaluate.return_value = "input#username\nbutton#login-btn"

    mock_locator = AsyncMock()
    mock_locator.first = mock_locator
    mock_page.locator = MagicMock(return_value=mock_locator)

    driver = ActionDriver(api_key="sk-test-mock-key")

    with patch("openai.OpenAI") as mock_openai_cls:
        mock_client = MagicMock()
        mock_client.chat.completions.create.return_value = mock_completion
        mock_openai_cls.return_value = mock_client

        steps = await driver.execute_goal("Log in as user alice", mock_page)

        assert len(steps) == 2
        assert steps[0]["action"] == "fill"
        assert steps[1]["action"] == "click"
        assert steps[0]["status"] == "completed"
        assert steps[1]["status"] == "completed"
