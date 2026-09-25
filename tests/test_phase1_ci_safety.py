"""Phase 1 CI safety, origin policy, pre-browser validation, and redaction tests."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
from click.testing import CliRunner
from pydantic import ValidationError
from rich.console import Console

from aiqa.cli import cli
from aiqa.executor.action_driver import ActionDriver
from aiqa.models.test_case import (
    Expectation,
    FillAction,
    NavigateAction,
    RunSummary,
    TestCase,
    TestResult,
    TestRunReport,
    TestSuite,
    VerificationResult,
)
from aiqa.orchestrator.runner import TestRunner
from aiqa.planner.site_inspector import InspectedPage
from aiqa.planner.test_generator import TestPlanner
from aiqa.reports.html_report import HtmlReporter
from aiqa.reports.json_report import JsonReporter
from aiqa.reports.junit_report import JUnitReporter
from aiqa.security.policy import (
    extract_origin,
    is_origin_allowed,
    is_safe_url_scheme,
    validate_test_suite_policy,
)
from aiqa.security.redaction import (
    clear_registered_secrets,
    redact_data,
    redact_text,
    register_secret,
)


def test_phase1_duplicate_test_ids_and_timeout_bounds_rejected() -> None:
    """Suite schema rejects duplicate test IDs and out-of-range timeouts."""
    case1 = TestCase(
        id="DUP-001",
        name="First test",
        start_url="https://example.test",
        expected=[Expectation(type="url", description="URL matches", value="example.test")],
    )
    case2 = TestCase(
        id="DUP-001",
        name="Duplicate ID test",
        start_url="https://example.test",
        expected=[Expectation(type="url", description="URL matches", value="example.test")],
    )
    with pytest.raises(ValidationError, match="Duplicate test case IDs"):
        TestSuite(name="Dup Suite", base_url="https://example.test", tests=[case1, case2])

    with pytest.raises(ValidationError):
        TestCase(
            id="TIMEOUT-ZERO",
            name="Zero timeout",
            start_url="https://example.test",
            expected=[Expectation(type="url", description="URL", value="example.test")],
            timeout=0,
        )

    with pytest.raises(ValidationError):
        TestCase(
            id="TIMEOUT-TOO-LARGE",
            name="Huge timeout",
            start_url="https://example.test",
            expected=[Expectation(type="url", description="URL", value="example.test")],
            timeout=9999,
        )


def test_phase1_url_scheme_and_origin_policy_helpers() -> None:
    """Verify safe URL scheme checks and origin policy enforcement."""
    assert is_safe_url_scheme("https://example.com/login") is True
    assert is_safe_url_scheme("http://localhost:3000/cart") is True
    assert is_safe_url_scheme("/relative/path") is True
    assert is_safe_url_scheme("about:blank") is True
    assert is_safe_url_scheme("javascript:alert(1)") is False
    assert is_safe_url_scheme("file:///etc/passwd") is False

    assert extract_origin("https://App.Example.com:8443/dashboard") == "https://app.example.com:8443"
    assert extract_origin("/relative") is None

    allowed = ["https://app.example.com"]
    assert is_origin_allowed("https://app.example.com/settings", allowed) is True
    assert is_origin_allowed("/settings", allowed) is True
    assert is_origin_allowed("https://evil.example.org/phish", allowed) is False
    assert (
        is_origin_allowed("https://evil.example.org/phish", allowed, allow_cross_origin=True)
        is True
    )


def test_phase1_cli_rejects_cross_origin_suite_before_browser_launch(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """CLI validates origin policy before instantiating or launching a browser."""
    browser_launched = False

    class ForbiddenBrowserRunner:
        def __init__(self, **_kwargs: object) -> None:
            nonlocal browser_launched
            browser_launched = True

    monkeypatch.setattr("aiqa.orchestrator.runner.TestRunner", ForbiddenBrowserRunner)

    cross_suite = TestSuite(
        name="Cross Origin Suite",
        base_url="https://app.example.test",
        tests=[
            TestCase(
                id="CROSS-001",
                name="Navigate to external domain",
                start_url="https://app.example.test",
                actions=[NavigateAction(action="navigate", url="https://external.attacker.test")],
                expected=[
                    Expectation(
                        type="url",
                        description="URL matches",
                        value="external.attacker.test",
                    )
                ],
            )
        ],
    )
    suite_path = tmp_path / "cross_suite.json"
    suite_path.write_text(cross_suite.model_dump_json(indent=2), encoding="utf-8")

    res = CliRunner().invoke(
        cli,
        [
            "test",
            "--url",
            "https://app.example.test",
            "--tests",
            str(suite_path),
        ],
    )
    assert res.exit_code == 1
    assert "Policy Validation Error" in res.output
    assert "external.attacker.test" in res.output
    assert validate_test_suite_policy(cross_suite)
    assert browser_launched is False


@pytest.mark.asyncio
async def test_phase1_runner_blocks_cross_origin_unless_opted_in(tmp_path: Path) -> None:
    """TestRunner blocks cross-origin navigation by default and allows with opt-in."""
    runner = TestRunner(
        headless=True,
        screenshots_dir=tmp_path / "shots",
        console=Console(quiet=True),
        allowed_origins=["https://app.example.test"],
    )

    cross_case = TestCase(
        id="CROSS-NAV-001",
        name="Attempt cross-origin navigate",
        start_url="https://app.example.test/home",
        actions=[NavigateAction(action="navigate", url="https://other-origin.test/steal")],
        expected=[Expectation(type="url", description="URL", value="other-origin")],
    )
    res_blocked = await runner.run_test(cross_case)
    assert res_blocked.status == "fail"
    assert "violates origin constraint policy" in (res_blocked.error_message or "")

    # Also verify ActionDriver blocks cross-origin navigate directly
    driver = ActionDriver(allowed_origins=["https://app.example.test"], allow_cross_origin=False)
    mock_page = AsyncMock()
    outcomes = await driver.execute_goal(
        "Navigate away",
        mock_page,
        actions=[NavigateAction(action="navigate", url="https://evil.test")],
    )
    assert outcomes[0]["status"] == "failed"
    assert outcomes[0]["error"]["code"] == "cross_origin_violation"
    mock_page.goto.assert_not_called()


def test_phase1_planner_omits_cross_origin_links_by_default() -> None:
    """Heuristic planner omits cross-origin nav links unless allow_cross_origin=True."""
    inspected = InspectedPage(
        url="https://shop.example.test",
        title="Shop Home",
        nav_links=[
            {"text": "External Blog", "href": "https://blog.external.org/news"},
            {"text": "Local Catalog", "href": "https://shop.example.test/catalog"},
        ],
    )
    planner = TestPlanner()
    default_suite = planner.generate_suite(inspected, max_tests=5)
    nav_tests = [t for t in default_suite.tests if t.id.startswith("NAV-")]
    assert len(nav_tests) == 1
    assert nav_tests[0].name == "Navigate to 'Local Catalog'"
    assert any(
        "Omitted 1 cross-origin navigation link(s)" in n for n in default_suite.planning_notes
    )

    opt_in_suite = planner.generate_suite(inspected, max_tests=5, allow_cross_origin=True)
    opt_in_nav = [t for t in opt_in_suite.tests if t.id.startswith("NAV-")]
    assert len(opt_in_nav) == 2


@pytest.mark.asyncio
async def test_phase1_secret_redaction_across_driver_and_reports(tmp_path: Path) -> None:
    """Secrets, passwords, API keys, and query tokens are redacted from traces and reports."""
    clear_registered_secrets()
    register_secret("MyDynamicVaultSecret99!")
    assert "[REDACTED]" in redact_text("Vault: MyDynamicVaultSecret99!")
    assert redact_data({"password": "SecretValue123"})["password"] == "[REDACTED]"

    driver = ActionDriver()
    mock_page = AsyncMock()
    mock_page.locator = MagicMock()
    mock_page.locator.return_value.first.fill = AsyncMock()
    outcomes = await driver.execute_goal(
        "Login with password",
        mock_page,
        actions=[
            FillAction(
                action="fill",
                selector="#user-password",
                value="SuperSecretPassword123!",
                description="Type SuperSecretPassword123! into password field",
            )
        ],
    )
    assert outcomes[0]["status"] == "completed"
    assert "SuperSecretPassword123!" not in outcomes[0]["details"]
    assert "[REDACTED]" in outcomes[0]["details"]

    # Build a report containing various secret leaks in logs, errors, and URLs
    exp = Expectation(
        type="url",
        description="Redirected with token",
        value="https://app.example.test/callback",
    )
    vr = VerificationResult(
        expectation=exp,
        passed=False,
        actual_value="https://app.example.test/callback?access_token=eyJhbGciOiJIUzI1NiJ9&api_key=sk-1234567890abcdef",
        message="Failed with Bearer eyJSecretBearerToken123 and vault MyDynamicVaultSecret99!",
    )
    res = TestResult(
        test_id="SEC-001",
        status="fail",
        duration_seconds=1.2,
        jev_steps=outcomes,
        verification_results=[vr],
        error_message="Auth failed: password=' PlaintextPass! ' key=ghp_1234567890abcdefghijklmnop",
        timestamp=datetime.now(UTC),
        console_logs=[{"type": "error", "text": "Token leak: Bearer abcdef123456"}],
        network_errors=[{"url": "https://api.example.test/v1?secret=TopSecretParam", "status": 401}],
    )
    report = TestRunReport(
        run_id="sec-run-1",
        suite_name="Security Suite",
        base_url="https://app.example.test?token=BaseUrlSecretToken",
        started_at=datetime.now(UTC),
        finished_at=datetime.now(UTC),
        results=[res],
        summary=RunSummary.from_results([res], duration_seconds=1.2),
    )

    json_path = JsonReporter(output_dir=tmp_path).save(report)
    junit_path = JUnitReporter(output_dir=tmp_path).save(report)
    html_path = HtmlReporter(output_dir=tmp_path).save(report)

    for artifact in (json_path, junit_path, html_path):
        content = artifact.read_text(encoding="utf-8")
        assert "SuperSecretPassword123!" not in content
        assert "MyDynamicVaultSecret99!" not in content
        assert "eyJhbGciOiJIUzI1NiJ9" not in content
        assert "sk-1234567890abcdef" not in content
        assert "eyJSecretBearerToken123" not in content
        assert "ghp_1234567890abcdefghijklmnop" not in content
        assert "TopSecretParam" not in content
        assert "BaseUrlSecretToken" not in content
        assert "[REDACTED]" in content

    clear_registered_secrets()
