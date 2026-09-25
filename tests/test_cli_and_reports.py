"""Tests for JsonReporter and CLI components."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest
from click.testing import CliRunner

import aiqa.cli as cli_module
from aiqa.cli import cli
from aiqa.models.test_case import (
    Expectation,
    RunSummary,
    TestCase,
    TestResult,
    TestRunReport,
    TestSuite,
    VerificationResult,
)
from aiqa.reports.json_report import JsonReporter

TestCase.__test__ = False
TestResult.__test__ = False
TestRunReport.__test__ = False
TestSuite.__test__ = False


@pytest.fixture
def sample_test_result() -> TestResult:
    exp = Expectation(
        type="dom",
        description="Cart badge displays 1",
        selector=".cart-count",
        value="1",
    )
    vr = VerificationResult(
        expectation=exp,
        passed=True,
        actual_value="1",
        message="Cart badge verified",
    )
    return TestResult(
        test_id="CART-001",
        status="pass",
        duration_seconds=1.45,
        jev_steps=[{"step": 1, "action": "click"}],
        verification_results=[vr],
        timestamp=datetime.now(UTC),
    )


@pytest.fixture
def sample_test_run_report(sample_test_result: TestResult) -> TestRunReport:
    summary = RunSummary.from_results([sample_test_result], duration_seconds=1.45)
    return TestRunReport(
        run_id="run-uuid-12345",
        suite_name="Shopping Tests",
        base_url="http://localhost:3000",
        started_at=datetime.now(UTC),
        finished_at=datetime.now(UTC),
        results=[sample_test_result],
        summary=summary,
    )


def test_json_reporter_save_and_load(tmp_path: Path, sample_test_run_report: TestRunReport):
    reporter = JsonReporter(output_dir=tmp_path)
    saved_file = reporter.save(sample_test_run_report)

    assert saved_file.exists()
    assert saved_file.suffix == ".json"
    assert saved_file.name.startswith("run_")

    loaded_report = JsonReporter.load(saved_file)
    assert loaded_report.run_id == sample_test_run_report.run_id
    assert loaded_report.suite_name == "Shopping Tests"
    assert loaded_report.summary.total == 1
    assert loaded_report.summary.passed == 1
    assert loaded_report.summary.failed == 0


def test_json_report_contains_stable_schema_and_runtime_metadata(
    tmp_path: Path, sample_test_run_report: TestRunReport
) -> None:
    import json

    saved_file = JsonReporter(output_dir=tmp_path).save(sample_test_run_report)
    payload = json.loads(saved_file.read_text(encoding="utf-8"))

    assert payload["schema_version"] == "1.0"
    assert "$schema" not in payload
    assert payload["metadata"]["runtime"]["python"]
    assert payload["metadata"]["runtime"]["platform"]
    assert payload["metadata"]["generated_at"].endswith("+00:00")


def test_json_reporter_format_summary(sample_test_run_report: TestRunReport):
    reporter = JsonReporter()
    summary_text = reporter.format_summary(sample_test_run_report)

    assert "AIQA Test Run Summary" in summary_text
    assert "Shopping Tests" in summary_text
    assert "CART-001" in summary_text
    assert "Passed:      1" in summary_text
    assert "Failed:      0" in summary_text


def test_cli_help():
    runner = CliRunner()
    result = runner.invoke(cli, ["--help"])
    assert result.exit_code == 0
    assert "AIQA — AI-Powered Autonomous Website Testing" in result.output
    assert "test" in result.output


def test_cli_test_help():
    runner = CliRunner()
    result = runner.invoke(cli, ["test", "--help"])
    assert result.exit_code == 0
    assert "--url" in result.output
    assert "--tests" in result.output
    assert "--headless" in result.output
    assert "--cdp" in result.output
    assert "--profile" in result.output
    assert "--storage-state" in result.output
    assert "--reuse-existing-context" in result.output
    assert "--reuse-existing-page" in result.output
    assert "--output" in result.output
    assert "--screenshots" in result.output
    assert "--html" in result.output
    assert "--junit" in result.output


def test_cli_plan_help():
    runner = CliRunner()
    result = runner.invoke(cli, ["plan", "--help"])
    assert result.exit_code == 0
    assert "--url" in result.output
    assert "--goal" in result.output
    assert "--output" in result.output
    assert "--cdp" in result.output
    assert "--max-tests" in result.output


def test_cli_coverage_help():
    runner = CliRunner()
    result = runner.invoke(cli, ["coverage", "--help"])
    assert result.exit_code == 0
    assert "--url" in result.output
    assert "--tests" in result.output
    assert "--max-pages" in result.output
    assert "--cdp" in result.output
    assert "--output" in result.output


def test_cli_report_help():
    runner = CliRunner()
    result = runner.invoke(cli, ["report", "--help"])
    assert result.exit_code == 0
    assert "--input" in result.output
    assert "--html" in result.output


def test_cli_report_generate_from_json(tmp_path: Path, sample_test_run_report: TestRunReport):
    # Save a JSON report first
    reporter = JsonReporter(output_dir=tmp_path)
    saved_json = reporter.save(sample_test_run_report)

    html_out = tmp_path / "cli_dashboard.html"
    runner = CliRunner()
    result = runner.invoke(cli, ["report", "--input", str(saved_json), "--html", str(html_out)])

    assert result.exit_code == 0
    assert "Interactive HTML Dashboard generated successfully" in result.output
    assert html_out.exists()
    assert "AIQA Test Execution Dashboard" in html_out.read_text(encoding="utf-8")


def test_cli_auto_help():
    runner = CliRunner()
    result = runner.invoke(cli, ["auto", "--help"])
    assert result.exit_code == 0
    assert "--url" in result.output
    assert "--goal" in result.output
    assert "--max-tests" in result.output
    assert "--headless" in result.output
    assert "--cdp" in result.output
    assert "--output-dir" in result.output
    assert "--open" in result.output
    assert "--junit" in result.output
    assert "--storage-state" in result.output
    assert "--reuse-existing-context" in result.output
    assert "--reuse-existing-page" in result.output


def test_cli_missing_tests_file():
    runner = CliRunner()
    result = runner.invoke(
        cli, ["test", "--url", "http://localhost:3000", "--tests", "nonexistent.json"]
    )
    assert result.exit_code != 0
    assert "does not exist" in result.output


@pytest.mark.parametrize(
    ("browser_options", "expected_message"),
    [
        (["--reuse-existing-context"], "requires --cdp"),
        (
            ["--cdp", "http://127.0.0.1:9222", "--reuse-existing-page"],
            "requires --reuse-existing-context",
        ),
        (["--storage-state", "state.json", "--profile", "profile"], "cannot be combined"),
        (
            [
                "--storage-state",
                "state.json",
                "--cdp",
                "http://127.0.0.1:9222",
                "--reuse-existing-context",
            ],
            "cannot be combined",
        ),
    ],
)
def test_cli_rejects_unsafe_browser_mode_combinations(
    tmp_path: Path,
    browser_options: list[str],
    expected_message: str,
) -> None:
    suite_file = tmp_path / "suite.json"
    suite_file.write_text("{}", encoding="utf-8")
    state_file = tmp_path / "state.json"
    state_file.write_text("{}", encoding="utf-8")
    resolved_options = [
        str(state_file) if value == "state.json" else str(tmp_path / "profile") if value == "profile" else value
        for value in browser_options
    ]

    result = CliRunner().invoke(
        cli,
        [
            "test",
            "--url",
            "https://example.test",
            "--tests",
            str(suite_file),
            *resolved_options,
        ],
    )

    assert result.exit_code == 2
    assert expected_message in result.output


@pytest.mark.parametrize(
    ("report_kind", "method_path", "expected_message"),
    [
        ("json", "aiqa.reports.json_report.JsonReporter.save", "Failed to save JSON report"),
        ("html", "aiqa.reports.html_report.HtmlReporter.save", "Failed to save HTML report"),
        (
            "junit",
            "aiqa.reports.junit_report.JUnitReporter.save",
            "Failed to save JUnit report",
        ),
    ],
)
def test_cli_test_exits_nonzero_when_a_report_cannot_be_written(
    tmp_path: Path,
    sample_test_run_report: TestRunReport,
    monkeypatch: pytest.MonkeyPatch,
    report_kind: str,
    method_path: str,
    expected_message: str,
) -> None:
    suite = TestSuite(
        name="Shopping Tests",
        base_url="http://localhost:3000",
        tests=[
            TestCase(
                id="CART-001",
                name="Add to cart",
                start_url="/",
                goal="Click add to cart",
                expected=[
                    Expectation(
                        type="dom",
                        description="Cart badge displays 1",
                        selector=".cart-count",
                        value="1",
                    )
                ],
            )
        ],
    )
    suite_file = tmp_path / "suite.json"
    suite_file.write_text(suite.model_dump_json(), encoding="utf-8")

    class FakeRunner:
        def __init__(self, **_kwargs: object) -> None:
            pass

        async def run_suite(self, _suite: TestSuite) -> TestRunReport:
            return sample_test_run_report

    def fail_save(*_args: object, **_kwargs: object) -> Path:
        raise OSError("read-only report destination")

    monkeypatch.setattr("aiqa.orchestrator.runner.TestRunner", FakeRunner)
    monkeypatch.setattr(method_path, fail_save)

    report_option: list[str] = []
    if report_kind in {"html", "junit"}:
        report_option = [f"--{report_kind}", str(tmp_path / "blocked" / f"results.{report_kind}")]

    result = CliRunner().invoke(
        cli,
        [
            "test",
            "--url",
            "http://localhost:3000",
            "--tests",
            str(suite_file),
            "--output",
            str(tmp_path / "reports"),
            *report_option,
        ],
    )

    assert result.exit_code == 1
    assert expected_message in result.output


@pytest.mark.asyncio
async def test_auto_forwards_requested_junit_output(
    tmp_path: Path,
    sample_test_run_report: TestRunReport,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    suite = TestSuite(name="Auto suite", base_url="https://example.test", tests=[])
    observed: dict[str, object] = {}

    class FakeInspector:
        def __init__(self, **_kwargs: object) -> None:
            pass

        async def inspect(self, url: str) -> object:
            return {"url": url}

    class FakePlanner:
        def generate_suite(self, *_args: object, **_kwargs: object) -> TestSuite:
            return suite

        def save_suite(self, generated: TestSuite, path: Path) -> None:
            path.write_text(generated.model_dump_json(), encoding="utf-8")

    async def fake_run_tests(**kwargs: object) -> TestRunReport:
        observed.update(kwargs)
        return sample_test_run_report

    monkeypatch.setattr("aiqa.planner.SiteInspector", FakeInspector)
    monkeypatch.setattr("aiqa.planner.TestPlanner", FakePlanner)
    monkeypatch.setattr(cli_module, "_run_tests", fake_run_tests)
    junit_path = tmp_path / "ci" / "auto.xml"

    report = await cli_module._run_auto(
        url="https://example.test",
        output_dir=tmp_path,
        junit_path=junit_path,
    )

    assert report is sample_test_run_report
    assert observed["junit_path"] == junit_path
