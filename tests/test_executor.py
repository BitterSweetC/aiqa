"""Unit tests for AIQA executor: BrowserSession and JevRunner."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from aiqa.executor.browser_session import BrowserSession
from aiqa.executor.jev_runner import JevExecutionResult, JevRunner
from aiqa.models.test_case import Expectation, TestCase

TestCase.__test__ = False


@pytest.mark.asyncio
async def test_browser_session_not_active_raises():
    """Accessing session.page before entering context should raise RuntimeError."""
    session = BrowserSession(headless=True)
    with pytest.raises(RuntimeError, match="Browser session is not active"):
        _ = session.page


@pytest.mark.asyncio
async def test_browser_session_lifecycle(tmp_path: Path):
    """Test full lifecycle: start, navigate, get state, eval JS, screenshot, close."""
    session = BrowserSession(headless=True)
    assert not session.is_active

    async with session:
        assert session.is_active
        assert session.page is not None

        # Test navigation to data URI
        html_content = "<html><head><title>Test Page</title></head><body><h1>Hello AIQA</h1></body></html>"
        data_url = f"data:text/html,{html_content}"
        await session.goto(data_url)

        # Test page state
        state = await session.get_page_state()
        assert state["url"].startswith("data:text/html")
        assert state["title"] == "Test Page"
        assert "Hello AIQA" in state["html"]
        assert "Hello AIQA" in state["text"]

        # Test JS evaluation
        js_val = await session.evaluate_js("10 + 32")
        assert js_val == 42

        # Test screenshot
        ss_path = tmp_path / "test_ss.png"
        saved_path = await session.screenshot(ss_path)
        assert Path(saved_path).exists()
        assert Path(saved_path).stat().st_size > 0

    assert not session.is_active


@pytest.mark.asyncio
async def test_jev_runner_simulation_fallback(tmp_path: Path):
    """Test JevRunner with simulation fallback captures steps, DOM, and screenshots."""
    screenshots_dir = tmp_path / "screenshots"
    runner = JevRunner(screenshots_dir=screenshots_dir, force_mock=True)

    test = TestCase(
        id="SEARCH-001",
        name="Search flow test",
        start_url="data:text/html,<html><head><title>Search Store</title></head><body><input id='search' placeholder='Search products...'/><button id='btn'>Go</button></body></html>",
        goal="Search for monitor and press Go",
        actions=[
            {"action": "fill", "selector": "#search", "value": "monitor"},
            {"action": "click", "selector": "#btn"},
        ],
        expected=[
            Expectation(
                type="dom",
                description="Search input is displayed",
                selector="#search",
            )
        ],
    )

    async with BrowserSession(headless=True) as session:
        result = await runner.run(test, session)

    assert isinstance(result, JevExecutionResult)
    assert result.success is True
    assert result.error is None
    assert result.duration_seconds > 0
    assert len(result.steps) >= 3

    # Verify action trace structure
    step_actions = [step["action"] for step in result.steps]
    assert "navigate" in step_actions
    assert "dom_snapshot" in step_actions
    assert "execute_actions" in step_actions

    # Verify captured state and screenshot
    assert "Search Store" in result.page_html
    assert result.screenshot_path is not None
    assert Path(result.screenshot_path).exists()
    assert Path(result.screenshot_path).stat().st_size > 0

    # Verify to_dict
    res_dict = result.to_dict()
    assert res_dict["success"] is True
    assert res_dict["final_url"].startswith("data:text/html")


@pytest.mark.asyncio
async def test_jev_runner_managed_lifecycle(tmp_path: Path):
    """JevRunner automatically manages session lifecycle when not pre-started."""
    screenshots_dir = tmp_path / "screenshots_managed"
    runner = JevRunner(screenshots_dir=screenshots_dir)

    session = BrowserSession(headless=True)
    assert not session.is_active

    test = TestCase(
        id="AUTO-001",
        name="Auto session test",
        start_url="about:blank",
        goal="Verify blank page",
        actions=[{"action": "wait", "timeout_ms": 1}],
        expected=[],
    )

    result = await runner.run(test, session)
    assert result.success is True
    # Session should be closed after run completes
    assert not session.is_active


@pytest.mark.asyncio
async def test_jev_runner_error_handling(tmp_path: Path):
    """Test JevRunner handles navigation failure gracefully."""
    runner = JevRunner(screenshots_dir=tmp_path / "screenshots_err")

    test = TestCase(
        id="ERR-404",
        name="Invalid protocol test",
        start_url="http://non-existent-domain-12345.invalid:9999",
        goal="Should fail navigation",
        expected=[],
    )

    session = BrowserSession(headless=True)
    result = await runner.run(test, session)

    assert result.success is False
    assert result.error is not None
    assert result.duration_seconds >= 0


@pytest.mark.asyncio
async def test_jev_runner_propagates_failed_action_trace(tmp_path: Path):
    """A failed required browser action makes the execution result unsuccessful."""
    runner = JevRunner(screenshots_dir=tmp_path / "screenshots", force_mock=True)
    session = AsyncMock()
    session.is_active = True
    session.get_page_state.return_value = {
        "url": "about:blank",
        "html": "<html></html>",
    }
    session.screenshot.return_value = str(tmp_path / "failure.png")
    runner._run_simulation_fallback = AsyncMock(
        return_value=[
            {
                "step": 2,
                "action": "click",
                "status": "failed",
                "details": "Missing target",
                "error": {"code": "action_execution_failed", "message": "Missing target"},
            }
        ]
    )

    test = TestCase(
        id="ACTION-FAIL",
        name="Missing action target",
        start_url="about:blank",
        goal="Click missing target",
        actions=[{"action": "click", "selector": "#missing"}],
        expected=[Expectation(type="url", description="Remain blank", value="about:blank")],
    )
    result = await runner.run(test, session)

    assert result.success is False
    assert result.error == "Missing target"
    assert result.steps[-1]["status"] == "failed"


@pytest.mark.asyncio
async def test_jev_runner_rejects_trace_without_successful_required_action(tmp_path: Path):
    """DOM inspection alone cannot prove that the requested action executed."""
    runner = JevRunner(screenshots_dir=tmp_path / "screenshots", force_mock=True)
    session = AsyncMock()
    session.is_active = True
    page = MagicMock()
    page.url = "about:blank"
    locator = AsyncMock()
    locator.first = locator
    locator.count.return_value = 0
    page.locator.return_value = locator
    session.page = page
    session.get_page_state.return_value = {
        "url": "about:blank",
        "html": "<html><body>Already true</body></html>",
    }
    session.screenshot.return_value = str(tmp_path / "failure.png")
    runner._run_simulation_fallback = AsyncMock(
        return_value=[
            {
                "step": 2,
                "action": "dom_snapshot",
                "status": "completed",
                "details": "Inspected page",
            }
        ]
    )

    test = TestCase(
        id="EMPTY-TRACE",
        name="No action proof",
        start_url="about:blank",
        goal="Perform an action",
        expected=[Expectation(type="dom", description="Body exists", selector="body")],
    )
    result = await runner.run(test, session)

    assert result.success is False
    assert "successful required action" in (result.error or "")
    assert result.steps[-1]["error"]["code"] == "missing_action_outcome"
