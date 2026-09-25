"""Tests for AIQA TestRunner orchestrator."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock
from urllib.parse import quote
from uuid import UUID

import pytest
from rich.console import Console

from aiqa.models.test_case import (
    Expectation,
    TestCase,
    TestRunReport,
    TestSuite,
    VerificationResult,
)
from aiqa.orchestrator.runner import TestRunner


@pytest.fixture
def mock_runner(tmp_path: Path) -> TestRunner:
    """Provide a TestRunner configured with quiet console and tmp screenshots dir."""
    quiet_console = Console(quiet=True)
    runner = TestRunner(
        headless=True,
        screenshots_dir=tmp_path / "screenshots",
        console=quiet_console,
    )
    return runner


def test_runner_init(tmp_path: Path):
    """Verify TestRunner initialization attributes."""
    screenshots = tmp_path / "shots"
    runner = TestRunner(
        headless=False,
        screenshots_dir=screenshots,
        openai_api_key="sk-mock",
    )
    assert runner.headless is False
    assert runner.screenshots_dir == screenshots
    assert runner.dom_verifier is not None
    assert runner.url_verifier is not None
    assert runner.semantic_verifier is not None
    assert runner.jev_runner is not None


def test_route_verifier(mock_runner: TestRunner):
    """Verify routing of expectation types to respective verifiers."""
    assert mock_runner._route_verifier("dom") is mock_runner.dom_verifier
    assert mock_runner._route_verifier("dom_element") is mock_runner.dom_verifier
    assert mock_runner._route_verifier("selector") is mock_runner.dom_verifier
    assert mock_runner._route_verifier("url") is mock_runner.url_verifier
    assert mock_runner._route_verifier("url_path") is mock_runner.url_verifier
    assert mock_runner._route_verifier("semantic") is mock_runner.semantic_verifier
    assert mock_runner._route_verifier("llm") is mock_runner.semantic_verifier

    with pytest.raises(ValueError, match="Unknown verification expectation type"):
        mock_runner._route_verifier("unsupported_verifier_type")


@pytest.mark.asyncio
async def test_run_test_pass(mock_runner: TestRunner):
    """Verify a successful test run produces status 'pass'."""
    test = TestCase(
        id="TEST-001",
        name="Search test",
        start_url="about:blank",
        goal="Search for laptop",
        actions=[{"action": "navigate", "url": "about:blank"}],
        expected=[
            Expectation(type="dom", description="Results displayed", selector="body"),
        ],
    )

    mock_runner.dom_verifier.verify = AsyncMock(
        return_value=VerificationResult(
            expectation=test.expected[0],
            passed=True,
            message="Found body element",
        )
    )

    result = await mock_runner.run_test(test)
    assert result.test_id == "TEST-001"
    assert result.status == "pass"
    assert result.duration_seconds >= 0.0
    assert len(result.verification_results) == 1
    assert result.verification_results[0].passed is True
    assert result.error_message is None


@pytest.mark.asyncio
async def test_run_test_fail(mock_runner: TestRunner, tmp_path: Path):
    """Verify failed expectation marks test as 'fail' and takes screenshot."""
    test = TestCase(
        id="TEST-002",
        name="Cart test",
        start_url="about:blank",
        goal="Add item",
        actions=[{"action": "navigate", "url": "about:blank"}],
        expected=[
            Expectation(type="url", description="Cart URL", value="/cart"),
        ],
    )

    mock_runner.url_verifier.verify = AsyncMock(
        return_value=VerificationResult(
            expectation=test.expected[0],
            passed=False,
            message="Expected URL to contain /cart",
        )
    )

    result = await mock_runner.run_test(test)
    assert result.test_id == "TEST-002"
    assert result.status == "fail"
    assert result.error_message is not None
    assert "Expected URL to contain /cart" in result.error_message
    assert "TEST-002" in mock_runner._failed_test_ids


@pytest.mark.asyncio
async def test_run_test_error_on_jev_failure(mock_runner: TestRunner):
    """Key design rule: If Jev execution fails, mark test as ERROR (not FAIL)."""
    test = TestCase(
        id="TEST-003",
        name="Broken execution test",
        start_url="about:blank",
        goal="Crash the browser",
        actions=[{"action": "navigate", "url": "about:blank"}],
        expected=[
            Expectation(type="dom", description="Never reached", selector="div"),
        ],
    )

    mock_runner.jev_runner.run = AsyncMock(side_effect=RuntimeError("Browser process died"))

    result = await mock_runner.run_test(test)
    assert result.test_id == "TEST-003"
    assert result.status == "error"
    assert "Browser process died" in (result.error_message or "")
    assert "TEST-003" in mock_runner._failed_test_ids


@pytest.mark.asyncio
async def test_run_test_skip_preconditions(mock_runner: TestRunner):
    """Key design rule: SKIP if preconditions indicate dependency on a previously failed test."""
    mock_runner._failed_test_ids.add("PARENT-001")

    test = TestCase(
        id="CHILD-001",
        name="Dependent test",
        start_url="about:blank",
        preconditions=["PARENT-001"],
        goal="Perform action",
        expected=[],
    )

    # jev_runner should not be invoked when skipped
    mock_runner.jev_runner.run = AsyncMock()

    result = await mock_runner.run_test(test)
    assert result.test_id == "CHILD-001"
    assert result.status == "skip"
    assert result.duration_seconds == 0.0
    assert "PARENT-001" in (result.error_message or "")
    mock_runner.jev_runner.run.assert_not_called()


@pytest.mark.asyncio
async def test_run_suite_full_pipeline(mock_runner: TestRunner):
    """Verify running a full test suite with sequential tests and report generation."""
    test1 = TestCase(
        id="SEARCH-001",
        name="Search monitor",
        start_url="about:blank",
        goal="Search monitor",
        actions=[{"action": "navigate", "url": "about:blank"}],
        expected=[Expectation(type="dom", description="Found", selector="body")],
    )
    test2 = TestCase(
        id="CART-001",
        name="Add to cart",
        start_url="about:blank",
        goal="Add to cart",
        actions=[{"action": "navigate", "url": "about:blank"}],
        expected=[Expectation(type="url", description="In cart", value="/cart")],
    )
    test3 = TestCase(
        id="CART-002",
        name="Change quantity",
        start_url="about:blank",
        preconditions=["CART-001"],
        goal="Quantity 2",
        actions=[{"action": "navigate", "url": "about:blank"}],
        expected=[Expectation(type="dom", description="Quantity 2", selector="body")],
    )

    suite = TestSuite(
        name="Checkout Flow",
        base_url="about:blank",
        tests=[test1, test2, test3],
    )

    # test1 passes, test2 fails, test3 skips
    mock_runner.dom_verifier.verify = AsyncMock(
        return_value=VerificationResult(
            expectation=test1.expected[0],
            passed=True,
            message="DOM element verified",
        )
    )
    mock_runner.url_verifier.verify = AsyncMock(
        return_value=VerificationResult(
            expectation=test2.expected[0],
            passed=False,
            message="URL verification failed",
        )
    )

    report = await mock_runner.run_suite(suite)

    assert isinstance(report, TestRunReport)
    # Check UUID4 validity
    UUID(report.run_id)
    assert report.suite_name == "Checkout Flow"
    assert len(report.results) == 3
    assert report.summary.total == 3
    assert report.summary.passed == 1
    assert report.summary.failed == 1
    assert report.summary.skipped == 1
    assert report.summary.errors == 0
    assert report.summary.duration_seconds >= 0.0


@pytest.mark.asyncio
async def test_run_test_rejects_empty_postconditions_before_opening_browser(
    mock_runner: TestRunner,
):
    """A zero-assertion test cannot pass through vacuous truth."""
    test = TestCase(
        id="EMPTY-ASSERTIONS",
        name="No proof",
        start_url="about:blank",
        goal="Click save",
        actions=[{"action": "click", "selector": "#save"}],
        expected=[],
    )
    mock_runner._create_browser_session = MagicMock(
        side_effect=AssertionError("browser must not open")
    )

    result = await mock_runner.run_test(test)

    assert result.status == "fail"
    assert "postcondition" in (result.error_message or "").lower()
    assert "EMPTY-ASSERTIONS" in mock_runner._failed_test_ids


@pytest.mark.asyncio
async def test_run_test_rejects_assertion_that_only_proves_clicked_control_exists(
    mock_runner: TestRunner,
):
    """Finding the clicked element does not prove that the click had an effect."""
    test = TestCase(
        id="UNCHANGED-CONTROL",
        name="False click proof",
        start_url="about:blank",
        goal="Click save",
        actions=[{"action": "click", "selector": "#save"}],
        expected=[
            Expectation(
                type="dom",
                description="Save button remains present",
                selector="#save",
            )
        ],
    )

    result = await mock_runner.run_test(test)

    assert result.status == "fail"
    assert "effect" in (result.error_message or "").lower()


@pytest.mark.asyncio
async def test_run_test_rejects_nondeterministic_only_postconditions(mock_runner: TestRunner):
    """An interaction requires at least one deterministic DOM or URL check."""
    test = TestCase(
        id="SEMANTIC-ONLY",
        name="No deterministic proof",
        start_url="about:blank",
        goal="Click save",
        actions=[{"action": "click", "selector": "#save"}],
        expected=[Expectation(type="semantic", description="Looks successful")],
    )

    result = await mock_runner.run_test(test)

    assert result.status == "fail"
    assert "deterministic" in (result.error_message or "").lower()


@pytest.mark.asyncio
async def test_run_test_passes_when_action_effect_is_observed(mock_runner: TestRunner):
    """A successful action plus a changed deterministic postcondition passes."""
    html = (
        "<html><body><button id='save' "
        "onclick=\"document.querySelector('#status').textContent='saved'\">Save</button>"
        "<div id='status'>idle</div></body></html>"
    )
    test = TestCase(
        id="OBSERVED-EFFECT",
        name="Save updates status",
        start_url=f"data:text/html,{quote(html)}",
        goal="Save the form",
        actions=[{"action": "click", "selector": "#save"}],
        expected=[
            Expectation(
                type="dom",
                description="Status should contain 'saved'",
                selector="#status",
                value="saved",
            )
        ],
    )

    result = await mock_runner.run_test(test)

    assert result.status == "pass"


@pytest.mark.asyncio
async def test_run_test_fails_when_action_has_no_observable_effect(mock_runner: TestRunner):
    """An already-true assertion cannot prove that a no-op click worked."""
    html = (
        "<html><body><button id='save'>Save</button>"
        "<div id='status'>saved</div></body></html>"
    )
    test = TestCase(
        id="UNCHANGED-EFFECT",
        name="No-op save",
        start_url=f"data:text/html,{quote(html)}",
        goal="Save the form",
        actions=[{"action": "click", "selector": "#save"}],
        expected=[
            Expectation(
                type="dom",
                description="Status should contain 'saved'",
                selector="#status",
                value="saved",
            )
        ],
    )

    result = await mock_runner.run_test(test)

    assert result.status == "fail"
    assert "no observable" in (result.error_message or "").lower()


@pytest.mark.asyncio
async def test_runner_forwards_explicit_browser_isolation_options(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
):
    """Runner sessions preserve storage state and deliberate CDP reuse choices."""
    captured: dict[str, object] = {}

    class FakeBrowserSession:
        def __init__(self, **kwargs):
            captured.update(kwargs)

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

    monkeypatch.setattr("aiqa.orchestrator.runner.BrowserSession", FakeBrowserSession)
    storage_state = tmp_path / "auth.json"
    runner = TestRunner(
        cdp_url="http://127.0.0.1:9222",
        storage_state=storage_state,
        reuse_existing_context=False,
        reuse_existing_page=False,
    )

    async with runner._create_browser_session():
        pass

    assert captured["storage_state"] == storage_state
    assert captured["reuse_existing_context"] is False
    assert captured["reuse_existing_page"] is False


def test_runner_rejects_unsafe_browser_option_combinations(tmp_path: Path):
    """Invalid shared-state combinations fail before a browser is opened."""
    with pytest.raises(ValueError, match="requires reuse_existing_context"):
        TestRunner(cdp_url="http://127.0.0.1:9222", reuse_existing_page=True)
    with pytest.raises(ValueError, match="cannot be combined with user_data_dir"):
        TestRunner(
            user_data_dir=tmp_path / "profile",
            storage_state=tmp_path / "auth.json",
        )
