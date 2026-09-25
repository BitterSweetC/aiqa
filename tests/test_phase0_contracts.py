"""Phase 0 execution truth and test contract regression tests."""

from __future__ import annotations

import json
import threading
import xml.etree.ElementTree as ET
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest
from click.testing import CliRunner
from rich.console import Console

from aiqa.cli import cli
from aiqa.models.test_case import (
    ClickAction,
    Expectation,
    TestCase,
    TestSuite,
)
from aiqa.orchestrator.runner import TestRunner
from aiqa.planner.site_inspector import InspectedPage
from aiqa.planner.test_generator import TestPlanner

PHASE0_HTML = """<!DOCTYPE html>
<html>
<head><title>Phase 0 Contract Site</title></head>
<body>
    <h1>Phase 0 Portal</h1>
    <button id="counter-btn" onclick="document.getElementById('status').textContent='Updated'">
        Update Status
    </button>
    <div id="status">Initial</div>
</body>
</html>
"""


class _Phase0Handler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        body = PHASE0_HTML.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: object) -> None:
        pass


@pytest.fixture(scope="module")
def local_site_url() -> Iterator[str]:
    server = HTTPServer(("127.0.0.1", 0), _Phase0Handler)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{port}"
    finally:
        server.shutdown()
        server.server_close()


@pytest.mark.asyncio
async def test_phase0_false_positive_matrix_on_local_site(
    local_site_url: str,
    tmp_path: Path,
) -> None:
    """Verify the entire Phase 0 false-positive matrix against a local test site."""
    runner = TestRunner(
        headless=True,
        screenshots_dir=tmp_path / "screenshots",
        console=Console(quiet=True),
    )

    # 1. Valid interaction + observed deterministic effect -> PASS
    valid_case = TestCase(
        id="P0-PASS-001",
        name="Click button and observe status update",
        start_url=local_site_url,
        goal="Click #counter-btn and check status becomes Updated",
        actions=[ClickAction(action="click", selector="#counter-btn")],
        expected=[
            Expectation(
                type="dom",
                description="Status text should equal Updated",
                selector="#status",
                value="Updated",
            )
        ],
    )
    res_pass = await runner.run_test(valid_case)
    assert res_pass.status == "pass"
    assert any(s.get("status") == "completed" for s in res_pass.jev_steps)
    assert all(vr.passed for vr in res_pass.verification_results)

    # 2. Missing target selector -> FAIL
    missing_target = TestCase(
        id="P0-FAIL-MISSING",
        name="Click missing selector",
        start_url=local_site_url,
        goal="Click #does-not-exist",
        actions=[ClickAction(action="click", selector="#does-not-exist")],
        expected=[
            Expectation(
                type="dom",
                description="Status text is Initial",
                selector="#status",
                value="Initial",
            )
        ],
        timeout=5,
    )
    res_missing = await runner.run_test(missing_target)
    assert res_missing.status in ("fail", "error")

    # 3. Unsupported goal with no actions -> FAIL
    unsupported_goal = TestCase(
        id="P0-FAIL-UNSUPPORTED-GOAL",
        name="Unsupported natural language goal",
        start_url=local_site_url,
        goal="Perform magic gesture on the screen",
        actions=None,
        expected=[
            Expectation(
                type="dom",
                description="Status text is Initial",
                selector="#status",
                value="Initial",
            )
        ],
    )
    res_unsupported = await runner.run_test(unsupported_goal)
    assert res_unsupported.status in ("fail", "error")

    # 4. Empty action plan -> FAIL
    empty_plan = TestCase(
        id="P0-FAIL-EMPTY-ACTIONS",
        name="Empty action list",
        start_url=local_site_url,
        goal="Click button",
        actions=[],
        expected=[
            Expectation(
                type="dom",
                description="Status text is Initial",
                selector="#status",
                value="Initial",
            )
        ],
    )
    res_empty_plan = await runner.run_test(empty_plan)
    assert res_empty_plan.status in ("fail", "error")

    # 5. Empty assertions -> FAIL
    empty_assertions = TestCase(
        id="P0-FAIL-EMPTY-EXPECTED",
        name="No expectations",
        start_url=local_site_url,
        goal="Click #counter-btn",
        actions=[ClickAction(action="click", selector="#counter-btn")],
        expected=[],
    )
    res_empty_exp = await runner.run_test(empty_assertions)
    assert res_empty_exp.status in ("fail", "error")

    # 6. Unchanged postcondition (only checking clicked button exists) -> FAIL
    unchanged_postcondition = TestCase(
        id="P0-FAIL-UNCHANGED-POSTCONDITION",
        name="Only check clicked button exists",
        start_url=local_site_url,
        goal="Click #counter-btn",
        actions=[ClickAction(action="click", selector="#counter-btn")],
        expected=[
            Expectation(
                type="dom",
                description="Button #counter-btn exists",
                selector="#counter-btn",
                value=None,
            )
        ],
    )
    res_unchanged = await runner.run_test(unchanged_postcondition)
    assert res_unchanged.status in ("fail", "error")


def test_phase0_cli_and_serialized_reports_agree(
    local_site_url: str,
    tmp_path: Path,
) -> None:
    """Verify CLI exit code and serialized JSON/JUnit reports agree for pass and fail."""
    cli_runner = CliRunner()

    # Failing suite
    failing_suite = TestSuite(
        name="Phase 0 Failing Suite",
        base_url=local_site_url,
        tests=[
            TestCase(
                id="CLI-FAIL-001",
                name="Unchanged postcondition check",
                start_url=local_site_url,
                goal="Click #counter-btn",
                actions=[ClickAction(action="click", selector="#counter-btn")],
                expected=[
                    Expectation(
                        type="dom",
                        description="Button still exists",
                        selector="#counter-btn",
                    )
                ],
            )
        ],
    )
    fail_suite_path = tmp_path / "fail_suite.json"
    fail_suite_path.write_text(failing_suite.model_dump_json(indent=2), encoding="utf-8")
    fail_reports_dir = tmp_path / "fail_reports"
    fail_junit_path = fail_reports_dir / "junit.xml"

    fail_res = cli_runner.invoke(
        cli,
        [
            "test",
            "--url",
            local_site_url,
            "--tests",
            str(fail_suite_path),
            "--output",
            str(fail_reports_dir),
            "--screenshots",
            str(tmp_path / "shots_fail"),
            "--junit",
            str(fail_junit_path),
        ],
    )
    assert fail_res.exit_code != 0
    json_files = list(fail_reports_dir.glob("run_*.json"))
    assert len(json_files) == 1
    report_data = json.loads(json_files[0].read_text(encoding="utf-8"))
    assert report_data["summary"]["failed"] + report_data["summary"]["errors"] == 1
    assert fail_junit_path.exists()
    junit_root = ET.parse(fail_junit_path).getroot()
    found_suite = junit_root.find("testsuite")
    suite_el = found_suite if found_suite is not None else junit_root
    assert int(suite_el.attrib.get("failures", "0")) + int(suite_el.attrib.get("errors", "0")) == 1

    # Passing suite
    passing_suite = TestSuite(
        name="Phase 0 Passing Suite",
        base_url=local_site_url,
        tests=[
            TestCase(
                id="CLI-PASS-001",
                name="Valid interaction",
                start_url=local_site_url,
                goal="Click #counter-btn",
                actions=[ClickAction(action="click", selector="#counter-btn")],
                expected=[
                    Expectation(
                        type="dom",
                        description="Status becomes Updated",
                        selector="#status",
                        value="Updated",
                    )
                ],
            )
        ],
    )
    pass_suite_path = tmp_path / "pass_suite.json"
    pass_suite_path.write_text(passing_suite.model_dump_json(indent=2), encoding="utf-8")
    pass_reports_dir = tmp_path / "pass_reports"
    pass_junit_path = pass_reports_dir / "junit.xml"

    pass_res = cli_runner.invoke(
        cli,
        [
            "test",
            "--url",
            local_site_url,
            "--tests",
            str(pass_suite_path),
            "--output",
            str(pass_reports_dir),
            "--screenshots",
            str(tmp_path / "shots_pass"),
            "--junit",
            str(pass_junit_path),
        ],
    )
    assert pass_res.exit_code == 0
    pass_json_files = list(pass_reports_dir.glob("run_*.json"))
    assert len(pass_json_files) == 1
    pass_report_data = json.loads(pass_json_files[0].read_text(encoding="utf-8"))
    assert pass_report_data["summary"]["passed"] == 1
    assert pass_report_data["summary"]["failed"] == 0


def test_phase0_planner_surfaces_omitted_interaction_notes() -> None:
    """Verify heuristic planner records planning_notes when omitting unverifiable buttons."""
    inspected = InspectedPage(
        url="https://example.test",
        title="Example Portal",
        buttons=[{"text": "Mystery Action", "selector": "#mystery-btn", "tag": "button"}],
    )
    planner = TestPlanner()
    suite = planner.generate_suite(inspected, max_tests=5)
    assert len(suite.planning_notes) == 1
    assert "Omitted 1 candidate button interaction(s)" in suite.planning_notes[0]
