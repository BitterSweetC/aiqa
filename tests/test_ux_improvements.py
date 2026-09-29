"""Tests for user-friendly CLI commands (doctor, init, smart defaults) and enhanced HTML dashboard."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest
from click.testing import CliRunner

from aiqa.cli import _load_dotenv_if_present, cli, normalize_url
from aiqa.models.test_case import (
    Expectation,
    TestResult,
    TestRunReport,
    VerificationResult,
    load_test_suite,
)
from aiqa.reports.html_report import HtmlReporter
from aiqa.reports.json_report import JsonReporter
from aiqa.security.policy import validate_test_suite_policy


def test_normalize_url_handles_bare_domains_and_localhost() -> None:
    """Bare domains default to https:// while localhost/127.0.0.1 default to http://."""
    assert normalize_url("example.com") == "https://example.com"
    assert normalize_url("books.toscrape.com/catalogue") == "https://books.toscrape.com/catalogue"
    assert normalize_url("localhost:3000") == "http://localhost:3000"
    assert normalize_url("127.0.0.1:8080/login") == "http://127.0.0.1:8080/login"
    assert normalize_url("https://already.https.test") == "https://already.https.test"
    assert normalize_url("http://already.http.test") == "http://already.http.test"
    # Unsafe schemes remain untouched so policy validation rejects them
    assert normalize_url("javascript:alert(1)") == "javascript:alert(1)"


def test_load_dotenv_if_present_loads_unset_vars(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Built-in .env loader populates unset environment variables without overriding existing ones."""
    env_file = tmp_path / ".env"
    env_file.write_text(
        "# Comment line\n"
        "AIQA_TEST_CUSTOM_VAR=loaded_from_dotenv\n"
        "AIQA_EXISTING_VAR='should_not_override'\n"
        'QUOTED_MODEL="gpt-4o-mini"\n',
        encoding="utf-8",
    )
    monkeypatch.delenv("AIQA_TEST_CUSTOM_VAR", raising=False)
    monkeypatch.setenv("AIQA_EXISTING_VAR", "already_set")
    monkeypatch.delenv("QUOTED_MODEL", raising=False)

    loaded = _load_dotenv_if_present(env_file)
    assert "AIQA_TEST_CUSTOM_VAR" in loaded
    assert loaded["AIQA_TEST_CUSTOM_VAR"] == "loaded_from_dotenv"
    assert loaded["QUOTED_MODEL"] == "gpt-4o-mini"
    assert "AIQA_EXISTING_VAR" not in loaded


def test_cli_doctor_json_and_rich_output() -> None:
    """`aiqa doctor` checks Python, Playwright, AI engine mode, and directories."""
    runner = CliRunner()
    res = runner.invoke(cli, ["doctor"])
    assert res.exit_code == 0
    assert "Python Runtime" in res.output
    assert "Playwright" in res.output
    assert "AI Planning Engine" in res.output

    res_json = runner.invoke(cli, ["doctor", "--json"])
    assert res_json.exit_code == 0
    payload = json.loads(res_json.output)
    assert "checks" in payload
    assert "ready" in payload
    check_names = {c["name"] for c in payload["checks"]}
    assert "Python Runtime" in check_names
    assert "Playwright Chromium" in check_names
    assert "AI Planning Engine" in check_names


def test_cli_init_scaffolds_valid_starter_suite_and_env(tmp_path: Path) -> None:
    """`aiqa init` creates a policy-compliant starter suite JSON and optional .env file."""
    runner = CliRunner()
    suite_out = tmp_path / "starter_suite.json"
    env_out = tmp_path / ".env"

    res = runner.invoke(
        cli,
        [
            "init",
            "--url",
            "example.com",
            "--goal",
            "Verify homepage heading and search navigation",
            "--output",
            str(suite_out),
            "--env-path",
            str(env_out),
        ],
    )
    assert res.exit_code == 0, res.output
    assert suite_out.exists()
    assert env_out.exists()

    suite = load_test_suite(suite_out)
    assert suite.base_url == "https://example.com"
    assert len(suite.tests) >= 2
    assert validate_test_suite_policy(suite) == []

    # Running again without --force refuses to overwrite
    res_dup = runner.invoke(
        cli,
        ["init", "--url", "example.com", "--output", str(suite_out)],
    )
    assert res_dup.exit_code != 0
    assert "--force" in res_dup.output

    # With --force it overwrites cleanly
    res_force = runner.invoke(
        cli,
        ["init", "--url", "example.org", "--output", str(suite_out), "--force"],
    )
    assert res_force.exit_code == 0
    reloaded = load_test_suite(suite_out)
    assert reloaded.base_url == "https://example.org"


def test_cli_test_uses_suite_base_url_when_url_flag_omitted(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`aiqa test --tests <file>` uses `suite.base_url` when `--url` is omitted."""
    runner = CliRunner()
    suite_file = tmp_path / "suite.json"
    runner.invoke(cli, ["init", "--url", "https://example.com", "--output", str(suite_file)])

    captured_base_url: list[str] = []

    class DummyRunner:
        def __init__(self, **_kwargs: object) -> None:
            pass

        async def run_suite(self, suite: object) -> TestRunReport:
            base_url = getattr(suite, "base_url", "")
            captured_base_url.append(base_url)
            now = datetime.now(UTC)
            return TestRunReport.create(
                run_id="run_test_optional_url",
                suite_name="Starter Suite",
                base_url=base_url,
                started_at=now,
                finished_at=now,
                results=[
                    TestResult(
                        test_id="SMOKE-001",
                        name="Smoke Test",
                        status="pass",
                        duration_seconds=0.1,
                        jev_steps=[],
                        verification_results=[],
                        timestamp=now,
                    )
                ],
            )

    monkeypatch.setattr("aiqa.orchestrator.runner.TestRunner", DummyRunner)
    res = runner.invoke(
        cli,
        [
            "test",
            "--tests",
            str(suite_file),
            "--output",
            str(tmp_path / "reports"),
        ],
    )
    assert res.exit_code == 0, res.output
    assert captured_base_url == ["https://example.com"]


def test_cli_report_auto_discovers_latest_json_report(tmp_path: Path) -> None:
    """`aiqa report` without `--input` automatically selects the latest JSON report in `--reports-dir`."""
    reports_dir = tmp_path / "reports"
    reports_dir.mkdir(parents=True, exist_ok=True)
    now = datetime.now(UTC)
    report = TestRunReport.create(
        run_id="run_latest_auto",
        suite_name="Auto Discovered Suite",
        base_url="https://example.com",
        started_at=now,
        finished_at=now,
        results=[
            TestResult(
                test_id="SMOKE-001",
                name="Homepage Smoke",
                status="pass",
                duration_seconds=0.25,
                jev_steps=[],
                verification_results=[],
                timestamp=now,
            )
        ],
    )
    JsonReporter(output_dir=reports_dir).save(report, filename="run_20260927_095000.json")

    html_out = reports_dir / "latest_dashboard.html"
    res = CliRunner().invoke(
        cli,
        [
            "report",
            "--reports-dir",
            str(reports_dir),
            "--html",
            str(html_out),
        ],
    )
    assert res.exit_code == 0, res.output
    assert html_out.exists()
    assert "Auto Discovered Suite" in html_out.read_text(encoding="utf-8")


def test_enhanced_html_dashboard_features(tmp_path: Path) -> None:
    """HTML dashboard includes copy re-run CLI, copy bug report markdown, expand/collapse all, inconclusive filter, and state_delta."""
    now = datetime.now(UTC)
    report = TestRunReport.create(
        run_id="run_ux_html",
        suite_name="UX Dashboard Suite",
        base_url="https://shop.example.com",
        started_at=now,
        finished_at=now,
        results=[
            TestResult(
                test_id="CART-001",
                name="Add Item to Cart",
                status="fail",
                inconclusive=True,
                duration_seconds=1.42,
                error_message="Business oracle missing for discount rule",
                jev_steps=[
                    {
                        "action": "click",
                        "selector": "#add-to-cart",
                        "status": "completed",
                        "details": "Clicked #add-to-cart",
                        "observed_url": "https://shop.example.com/cart",
                        "observed_title": "Shopping Cart",
                        "state_delta": {
                            "url_changed": True,
                            "title_changed": True,
                            "dom_changed": True,
                        },
                    }
                ],
                postcondition_state={
                    "pre_url": "https://shop.example.com/product/1",
                    "post_url": "https://shop.example.com/cart",
                    "url_changed": True,
                    "title_changed": True,
                    "dom_changed": True,
                },
                verification_results=[
                    VerificationResult(
                        expectation=Expectation(
                            type="business_rule",
                            description="Verify VIP discount calculation",
                        ),
                        passed=False,
                        actual_value="inconclusive",
                        message="Missing explicit oracle",
                    )
                ],
                timestamp=now,
            )
        ],
    )

    reporter = HtmlReporter(output_dir=tmp_path)
    saved = reporter.save(report, filename="ux_dashboard.html")
    html_text = saved.read_text(encoding="utf-8")

    # 1. Inconclusive filter tab
    assert 'data-filter="inconclusive"' in html_text
    assert "Inconclusive (1)" in html_text

    # 2. Expand All / Collapse All buttons
    assert "expandAll()" in html_text
    assert "collapseAll()" in html_text

    # 3. One-click Copy Re-run CLI (--select <ID>) and Copy Bug Report Markdown
    assert "copyRerunCommand(" in html_text
    assert "copyBugReport(" in html_text
    assert "--select" in html_text

    # 4. State delta & postcondition diff rendering
    assert "state_delta" in html_text
    assert "Postcondition State Diff" in html_text
