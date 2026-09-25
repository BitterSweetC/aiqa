"""Contract tests for the JUnit XML report."""

from __future__ import annotations

import json
import xml.etree.ElementTree as ET
from datetime import UTC, datetime
from pathlib import Path

import pytest

from aiqa.models.test_case import RunSummary, TestResult, TestRunReport
from aiqa.reports.junit_report import JUnitReporter

TestResult.__test__ = False
TestRunReport.__test__ = False


def _result(status: str, *, test_id: str, message: str | None = None) -> TestResult:
    return TestResult(
        test_id=test_id,
        status=status,
        duration_seconds=0.25,
        jev_steps=[
            {
                "action": "click",
                "status": "completed" if status == "pass" else "failed",
                "details": "Clicked <Checkout> & continued",
            }
        ],
        verification_results=[],
        error_message=message,
        screenshot_path=f"artifacts/{test_id}.png",
        timestamp=datetime(2026, 9, 25, 12, 0, tzinfo=UTC),
    )


@pytest.fixture
def mixed_report() -> TestRunReport:
    results = [
        _result("pass", test_id="PASS-1"),
        _result("fail", test_id="FAIL-1", message="Expected <h1> & did not find it"),
        _result("error", test_id="ERROR-1", message="Browser crashed"),
        _result("skip", test_id="SKIP-1", message="Unsupported fixture"),
    ]
    return TestRunReport(
        run_id="run-123",
        suite_name='Suite <nightly> & "smoke"',
        base_url="https://example.test",
        started_at=datetime(2026, 9, 25, 12, 0, tzinfo=UTC),
        finished_at=datetime(2026, 9, 25, 12, 1, tzinfo=UTC),
        results=results,
        summary=RunSummary.from_results(results, duration_seconds=60),
    )


def test_junit_report_maps_all_result_statuses_and_evidence(
    tmp_path: Path, mixed_report: TestRunReport
) -> None:
    saved = JUnitReporter(output_dir=tmp_path).save(mixed_report)

    root = ET.parse(saved).getroot()
    assert root.tag == "testsuites"
    suite = root.find("testsuite")
    assert suite is not None
    assert suite.attrib == {
        "name": 'Suite <nightly> & "smoke"',
        "tests": "4",
        "failures": "1",
        "errors": "1",
        "skipped": "1",
        "time": "60.000000",
        "timestamp": "2026-09-25T12:00:00+00:00",
    }

    cases = {case.attrib["name"]: case for case in suite.findall("testcase")}
    assert cases["PASS-1"].find("failure") is None
    assert cases["PASS-1"].find("error") is None
    assert cases["PASS-1"].find("skipped") is None
    assert cases["FAIL-1"].find("failure").text == "Expected <h1> & did not find it"
    assert cases["ERROR-1"].find("error").text == "Browser crashed"
    assert cases["SKIP-1"].find("skipped").attrib["message"] == "Unsupported fixture"

    evidence = json.loads(cases["FAIL-1"].findtext("system-out", default="{}"))
    assert evidence["action_outcomes"][0]["status"] == "failed"
    assert evidence["artifact_paths"] == {"screenshot": "artifacts/FAIL-1.png"}


def test_junit_report_honors_explicit_filename(
    tmp_path: Path, mixed_report: TestRunReport
) -> None:
    destination = tmp_path / "nested" / "results.xml"

    saved = JUnitReporter(output_dir=tmp_path).save(mixed_report, destination)

    assert saved == destination
    assert destination.exists()
