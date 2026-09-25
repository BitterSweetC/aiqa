"""
aiqa.orchestrator.runner
~~~~~~~~~~~~~~~~~~~~~~~~

The central test execution orchestrator for AIQA.
Coordinates test sessions, executes Jev goals, verifies outcomes,
tracks execution metrics, and produces structured test run reports.
"""

from __future__ import annotations

import inspect
import logging
import time
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urljoin
from uuid import uuid4

from rich.console import Console
from rich.table import Table

from aiqa.analyzer import FailureAnalyzer
from aiqa.executor.browser_session import BrowserSession
from aiqa.executor.jev_runner import JevRunner
from aiqa.models.test_case import (
    Expectation,
    FailureDiagnosis,
    RunSummary,
    TestCase,
    TestResult,
    TestRunReport,
    TestSuite,
    VerificationResult,
)
from aiqa.verifier.dom import DomVerifier
from aiqa.verifier.semantic import SemanticVerifier
from aiqa.verifier.url import UrlVerifier

logger = logging.getLogger(__name__)


def _build_test_result(
    test: TestCase,
    status: str,
    duration: float,
    verifications: list[VerificationResult],
    error: str | None = None,
    screenshot_path: str | None = None,
    jev_steps: list[dict[str, Any]] | None = None,
    console_logs: list[dict[str, Any]] | None = None,
    network_errors: list[dict[str, Any]] | None = None,
    diagnosis: FailureDiagnosis | None = None,
) -> TestResult:
    """Safely construct a TestResult model instance across varying model schemas."""
    test_id = getattr(test, "id", None) or getattr(test, "test_id", "") or "UNKNOWN"
    test_name = getattr(test, "name", None) or getattr(test, "test_name", "") or test_id
    norm_status = status.lower()
    if norm_status not in ("pass", "fail", "error", "skip"):
        norm_status = "error"

    now = datetime.now(UTC)
    steps = jev_steps if jev_steps is not None else []

    if hasattr(TestResult, "model_fields"):
        fields = TestResult.model_fields
        kwargs: dict[str, Any] = {}
        if "test_id" in fields:
            kwargs["test_id"] = test_id
        if "id" in fields:
            kwargs["id"] = test_id
        if "name" in fields:
            kwargs["name"] = test_name
        if "test_name" in fields:
            kwargs["test_name"] = test_name
        if "status" in fields:
            kwargs["status"] = norm_status
        if "duration_seconds" in fields:
            kwargs["duration_seconds"] = duration
        elif "duration" in fields:
            kwargs["duration"] = duration
        if "jev_steps" in fields:
            kwargs["jev_steps"] = steps
        if "verification_results" in fields:
            kwargs["verification_results"] = verifications
        elif "verifications" in fields:
            kwargs["verifications"] = verifications
        if "error_message" in fields:
            kwargs["error_message"] = error
        elif "error" in fields:
            kwargs["error"] = error
        if "screenshot_path" in fields:
            kwargs["screenshot_path"] = screenshot_path
        if "timestamp" in fields:
            kwargs["timestamp"] = now
        if "console_logs" in fields:
            kwargs["console_logs"] = console_logs or []
        if "network_errors" in fields:
            kwargs["network_errors"] = network_errors or []
        if "diagnosis" in fields:
            kwargs["diagnosis"] = diagnosis
        return TestResult(**kwargs)

    return TestResult(
        test_id=test_id,
        status=norm_status,
        duration_seconds=duration,
        jev_steps=steps,
        verification_results=verifications,
        error_message=error,
        screenshot_path=screenshot_path,
        timestamp=now,
        console_logs=console_logs or [],
        network_errors=network_errors or [],
        diagnosis=diagnosis,
    )


def _build_verification_result(
    passed: bool,
    expectation_type: str,
    message: str | None = None,
    actual: Any = None,
    expected: Any = None,
    details: dict[str, Any] | None = None,
    expectation: Any = None,
) -> VerificationResult:
    """Safely construct a VerificationResult model instance."""
    exp_obj: Any
    if isinstance(expectation, Expectation):
        exp_obj = expectation
    elif isinstance(expectation, dict):
        exp_obj = Expectation(**expectation)
    elif expectation is not None and hasattr(expectation, "type"):
        exp_obj = expectation
    else:
        norm_type = (
            expectation_type
            if expectation_type in ("dom", "url", "api", "visual", "semantic")
            else "dom"
        )
        exp_obj = Expectation(
            type=norm_type,
            description=message or f"{expectation_type} check",
            value=str(expected) if expected is not None else None,
        )

    msg = message or ("Verification passed" if passed else "Verification failed")

    if hasattr(VerificationResult, "model_fields"):
        fields = VerificationResult.model_fields
        kwargs: dict[str, Any] = {}
        if "expectation" in fields:
            kwargs["expectation"] = exp_obj
        if "passed" in fields:
            kwargs["passed"] = passed
        elif "success" in fields:
            kwargs["success"] = passed
        if "message" in fields:
            kwargs["message"] = msg
        elif "description" in fields:
            kwargs["description"] = msg
        if "actual_value" in fields:
            kwargs["actual_value"] = str(actual) if actual is not None else None
        elif "actual" in fields:
            kwargs["actual"] = actual
        if "details" in fields:
            kwargs["details"] = details
        if "expectation_type" in fields:
            kwargs["expectation_type"] = expectation_type
        return VerificationResult(**kwargs)

    return VerificationResult(
        expectation=exp_obj,
        passed=passed,
        actual_value=str(actual) if actual is not None else None,
        message=msg,
    )


def _build_run_summary(
    total: int,
    passed: int,
    failed: int,
    skipped: int,
    errors: int,
    duration: float,
) -> RunSummary:
    """Safely construct a RunSummary model instance."""
    pass_rate = (passed / total) if total > 0 else 0.0
    if hasattr(RunSummary, "model_fields"):
        fields = RunSummary.model_fields
        kwargs: dict[str, Any] = {}
        if "total" in fields:
            kwargs["total"] = total
        if "passed" in fields:
            kwargs["passed"] = passed
        if "failed" in fields:
            kwargs["failed"] = failed
        if "skipped" in fields:
            kwargs["skipped"] = skipped
        if "errors" in fields:
            kwargs["errors"] = errors
        elif "error" in fields:
            kwargs["error"] = errors
        if "duration_seconds" in fields:
            kwargs["duration_seconds"] = duration
        elif "duration" in fields:
            kwargs["duration"] = duration
        if "pass_rate" in fields:
            kwargs["pass_rate"] = pass_rate
        return RunSummary(**kwargs)

    return RunSummary(
        total=total,
        passed=passed,
        failed=failed,
        skipped=skipped,
        errors=errors,
        duration_seconds=duration,
        pass_rate=pass_rate,
    )


def _build_test_run_report(
    run_id: str,
    suite_name: str | None,
    base_url: str | None,
    started_at: datetime,
    finished_at: datetime,
    results: list[TestResult],
) -> TestRunReport:
    """Safely construct a TestRunReport model instance."""
    suite_name_str = suite_name or "Test Suite"
    base_url_str = base_url or ""
    duration = max(0.0, (finished_at - started_at).total_seconds())

    if hasattr(TestRunReport, "create"):
        return TestRunReport.create(
            run_id=run_id,
            suite_name=suite_name_str,
            base_url=base_url_str,
            started_at=started_at,
            finished_at=finished_at,
            results=results,
        )

    summary = _build_run_summary(
        total=len(results),
        passed=sum(1 for r in results if getattr(r, "status", "").lower() == "pass"),
        failed=sum(1 for r in results if getattr(r, "status", "").lower() == "fail"),
        skipped=sum(1 for r in results if getattr(r, "status", "").lower() == "skip"),
        errors=sum(1 for r in results if getattr(r, "status", "").lower() == "error"),
        duration=round(duration, 2),
    )

    if hasattr(TestRunReport, "model_fields"):
        fields = TestRunReport.model_fields
        kwargs: dict[str, Any] = {}
        if "run_id" in fields:
            kwargs["run_id"] = run_id
        elif "id" in fields:
            kwargs["id"] = run_id
        if "suite_name" in fields:
            kwargs["suite_name"] = suite_name_str
        elif "name" in fields:
            kwargs["name"] = suite_name_str
        if "base_url" in fields:
            kwargs["base_url"] = base_url_str
        if "started_at" in fields:
            kwargs["started_at"] = started_at
        if "finished_at" in fields:
            kwargs["finished_at"] = finished_at
        if "results" in fields:
            kwargs["results"] = results
        elif "test_results" in fields:
            kwargs["test_results"] = results
        if "summary" in fields:
            kwargs["summary"] = summary
        return TestRunReport(**kwargs)

    return TestRunReport(
        run_id=run_id,
        suite_name=suite_name_str,
        base_url=base_url_str,
        started_at=started_at,
        finished_at=finished_at,
        results=results,
        summary=summary,
    )


class TestRunner:
    """Orchestrates the full test execution pipeline."""

    __test__ = False

    def __init__(
        self,
        headless: bool = True,
        screenshots_dir: Path | None = None,
        openai_api_key: str | None = None,
        console: Console | None = None,
        cdp_url: str | None = None,
        user_data_dir: Path | str | None = None,
        storage_state: Path | str | dict[str, Any] | None = None,
        reuse_existing_context: bool = False,
        reuse_existing_page: bool = False,
    ):
        if reuse_existing_page and not reuse_existing_context:
            raise ValueError("reuse_existing_page requires reuse_existing_context")
        if (reuse_existing_context or reuse_existing_page) and not cdp_url:
            raise ValueError("Browser context/page reuse requires a CDP URL")
        if storage_state is not None and reuse_existing_context:
            raise ValueError(
                "storage_state cannot be loaded when reusing an existing CDP context"
            )
        if storage_state is not None and user_data_dir is not None:
            raise ValueError("storage_state cannot be combined with user_data_dir")

        self.headless = headless
        self.cdp_url = cdp_url
        self.user_data_dir = user_data_dir
        self.storage_state = storage_state
        self.reuse_existing_context = reuse_existing_context
        self.reuse_existing_page = reuse_existing_page
        self.screenshots_dir = (
            Path(screenshots_dir) if screenshots_dir is not None else Path("./screenshots")
        )

        try:
            self.jev_runner = JevRunner(screenshots_dir=screenshots_dir)
        except TypeError:
            self.jev_runner = JevRunner()

        self.dom_verifier = DomVerifier()
        self.url_verifier = UrlVerifier()

        try:
            self.semantic_verifier = SemanticVerifier(api_key=openai_api_key)
        except TypeError:
            try:
                self.semantic_verifier = SemanticVerifier(openai_api_key=openai_api_key)  # type: ignore[call-arg]
            except TypeError:
                self.semantic_verifier = SemanticVerifier()

        self.failure_analyzer = FailureAnalyzer(api_key=openai_api_key)
        self.console = console or Console()
        self._failed_test_ids: set[str] = set()
        self._current_base_url: str | None = None

    async def run_suite(self, suite: TestSuite) -> TestRunReport:
        """Run all tests in a suite sequentially."""
        run_id = str(uuid4())
        self._failed_test_ids.clear()
        started_at = datetime.now(UTC)
        suite_start_time = time.perf_counter()

        suite_name = getattr(suite, "name", None) or "Test Suite"
        base_url = getattr(suite, "base_url", "") or ""
        self._current_base_url = base_url

        tests: list[TestCase] = getattr(suite, "tests", [])
        total_tests = len(tests)

        self.console.print("\n[bold cyan]AIQA Test Runner[/bold cyan]")
        self.console.print(f"[dim]{'─' * 32}[/dim]")
        if base_url:
            self.console.print(f"[bold]Target:[/bold] {base_url}")
        self.console.print(f"[bold]Suite:[/bold]  {suite_name}")
        self.console.print(f"[bold]Tests:[/bold]  {total_tests} loaded")
        self.console.print(f"[dim]Run ID: {run_id}[/dim]")
        self.console.print("\n[bold]Running...[/bold]\n")

        results: list[TestResult] = []
        for test in tests:
            result = await self.run_test(test)
            results.append(result)

        suite_duration = time.perf_counter() - suite_start_time
        finished_at = datetime.now(UTC)

        passed = sum(1 for r in results if getattr(r, "status", "").lower() == "pass")
        failed = sum(1 for r in results if getattr(r, "status", "").lower() == "fail")
        errors = sum(1 for r in results if getattr(r, "status", "").lower() == "error")
        skipped = sum(1 for r in results if getattr(r, "status", "").lower() == "skip")
        total = len(results)

        # Print summary table
        table = Table(
            title=f"Suite Summary: {suite_name}",
            show_header=True,
            header_style="bold cyan",
        )
        table.add_column("Test ID", style="cyan", no_wrap=True)
        table.add_column("Test Name")
        table.add_column("Status", justify="center")
        table.add_column("Duration", justify="right")
        table.add_column("Details", style="dim")

        for test, result in zip(tests, results):
            t_id = getattr(test, "id", "") or getattr(result, "test_id", "")
            t_name = getattr(test, "name", "") or getattr(test, "goal", "") or t_id
            st_raw = getattr(result, "status", "")
            st = st_raw.upper()
            dur_val = getattr(result, "duration_seconds", getattr(result, "duration", 0.0))
            dur_str = "—" if st == "SKIP" else f"{dur_val:.1f}s"

            st_styled = {
                "PASS": "[green bold]PASS[/green bold]",
                "FAIL": "[red bold]FAIL[/red bold]",
                "ERROR": "[magenta bold]ERROR[/magenta bold]",
                "SKIP": "[yellow bold]SKIP[/yellow bold]",
            }.get(st, st)

            err = getattr(result, "error_message", None) or getattr(result, "error", "") or ""
            table.add_row(t_id, t_name, st_styled, dur_str, err[:60] if err else "")

        self.console.print()
        self.console.print(table)
        self.console.print(f"[bold]Total:[/bold]   {total}")
        self.console.print(f"[bold]Passed:[/bold]  [green]{passed}[/green]")
        self.console.print(f"[bold]Failed:[/bold]  [red]{failed}[/red]")
        if errors:
            self.console.print(f"[bold]Errors:[/bold]  [magenta]{errors}[/magenta]")
        self.console.print(f"[bold]Skipped:[/bold] [yellow]{skipped}[/yellow]")
        self.console.print(f"[dim]Duration: {suite_duration:.2f}s[/dim]\n")

        return _build_test_run_report(
            run_id=run_id,
            suite_name=suite_name,
            base_url=base_url,
            started_at=started_at,
            finished_at=finished_at,
            results=results,
        )

    async def run_test(self, test: TestCase) -> TestResult:
        """
        Execute a single test:
        1. Open browser session
        2. Run Jev with the test goal
        3. For each expected verification:
           - Route to appropriate verifier (dom/url/semantic)
        4. Determine overall PASS/FAIL
        5. Take screenshot on failure
        6. Return TestResult
        """
        test_id = getattr(test, "id", None) or getattr(test, "test_id", "") or "UNKNOWN"

        # Check preconditions: SKIP if depends on a previously failed test
        dep_failed = self._check_precondition_failure(test)
        if dep_failed:
            status = "skip"
            duration = 0.0
            error_msg = f"Skipped: dependency on failed test '{dep_failed}'"
            self._print_test_progress(test, status, duration)
            return _build_test_result(
                test=test,
                status=status,
                duration=duration,
                verifications=[],
                error=error_msg,
            )

        postcondition_error = self._postcondition_validation_error(test)
        if postcondition_error:
            self._failed_test_ids.add(test_id)
            self._print_test_progress(test, "fail", 0.0)
            return _build_test_result(
                test=test,
                status="fail",
                duration=0.0,
                verifications=[],
                error=postcondition_error,
            )

        # Resolve relative start_url if suite base_url is available
        base_url = self._current_base_url
        if base_url and hasattr(test, "start_url"):
            start_url = getattr(test, "start_url", "")
            if (
                start_url
                and not start_url.startswith(("http://", "https://", "file://", "about:"))
                and hasattr(test, "model_copy")
            ):
                test = test.model_copy(update={"start_url": urljoin(base_url, start_url)})

        start_time = time.perf_counter()
        verifications: list[VerificationResult] = []
        screenshot_path: str | None = None
        jev_steps: list[dict[str, Any]] = []
        interaction_unchanged = False

        try:
            # 1. Open browser session (each test gets its own session for clean state)
            async with self._create_browser_session() as session:
                # 2. Run Jev with the test goal
                jev_failed = False
                jev_error: str | None = None
                try:
                    jev_result = await self._execute_jev(session, test)
                    if jev_result is not None:
                        jev_steps = getattr(jev_result, "steps", [])
                        if getattr(jev_result, "screenshot_path", None):
                            screenshot_path = jev_result.screenshot_path

                        interaction_unchanged = (
                            getattr(jev_result, "state_changed", None) is False
                            and self._has_required_interaction(test, jev_steps)
                        )

                        if getattr(jev_result, "success", None) is False:
                            jev_failed = True
                            jev_error = getattr(jev_result, "error", None) or getattr(
                                jev_result, "message", "Jev execution indicated failure"
                            )
                        elif getattr(jev_result, "status", None) in (
                            "ERROR",
                            "FAILED",
                            "FAIL",
                            "error",
                            "fail",
                        ):
                            jev_failed = True
                            jev_error = (
                                getattr(jev_result, "error", None)
                                or f"Jev returned status {jev_result.status}"
                            )
                except Exception as exc:
                    jev_failed = True
                    jev_error = f"Jev execution error: {exc}"
                    logger.exception("Jev execution failed for test %s", test_id)

                # If Jev execution fails, mark test as ERROR (not FAIL)
                if jev_failed:
                    status = "error"
                    duration = round(time.perf_counter() - start_time, 2)
                    if not screenshot_path:
                        screenshot_path = await self._take_screenshot(session, test_id, "error")
                    self._failed_test_ids.add(test_id)
                    self._print_test_progress(test, status, duration)

                    telemetry = session.get_telemetry() if hasattr(session, "get_telemetry") else {}
                    c_logs = telemetry.get("console_logs", [])
                    n_errs = telemetry.get("network_errors", [])
                    p_state = None
                    try:
                        if hasattr(session, "get_page_state") and getattr(session, "is_active", False):
                            p_state = await session.get_page_state()
                    except Exception:
                        pass

                    temp_res = _build_test_result(
                        test=test,
                        status=status,
                        duration=duration,
                        verifications=[],
                        error=jev_error,
                        screenshot_path=screenshot_path,
                        jev_steps=jev_steps,
                        console_logs=c_logs,
                        network_errors=n_errs,
                    )
                    diag = self.failure_analyzer.diagnose(test, temp_res, p_state) if hasattr(self, "failure_analyzer") else None

                    return _build_test_result(
                        test=test,
                        status=status,
                        duration=duration,
                        verifications=[],
                        error=jev_error,
                        screenshot_path=screenshot_path,
                        jev_steps=jev_steps,
                        console_logs=c_logs,
                        network_errors=n_errs,
                        diagnosis=diag,
                    )

                # 3. For each expected verification:
                #    - Route to appropriate verifier (dom/url/semantic)
                expectations = (
                    getattr(test, "expected", None)
                    or getattr(test, "expectations", None)
                    or getattr(test, "verifications", None)
                    or []
                )

                for expectation in expectations:
                    exp_type = self._get_expectation_type(expectation)
                    try:
                        verifier = self._route_verifier(exp_type)
                        v_res = await self._run_verifier(verifier, session, expectation, exp_type)
                        verifications.append(v_res)
                    except Exception as v_err:
                        logger.exception("Error executing verification for test %s", test_id)
                        v_res = _build_verification_result(
                            passed=False,
                            expectation_type=exp_type,
                            message=f"Verification execution error: {v_err}",
                            expectation=expectation,
                        )
                        verifications.append(v_res)

                # 4. Determine overall PASS/FAIL
                # FAIL means Jev succeeded but verification didn't pass
                all_passed = all(
                    getattr(vr, "passed", getattr(vr, "success", False)) for vr in verifications
                ) and not interaction_unchanged
                status = "pass" if all_passed else "fail"

                # 5. Take screenshot on failure
                test_error: str | None = None
                if status == "fail":
                    screenshot_path = await self._take_screenshot(session, test_id, "fail")
                    self._failed_test_ids.add(test_id)
                    failed_msgs = [
                        getattr(vr, "message", None)
                        for vr in verifications
                        if not getattr(vr, "passed", getattr(vr, "success", False))
                    ]
                    if interaction_unchanged:
                        failed_msgs.append(
                            "Required interaction produced no observable DOM or URL change"
                        )
                    test_error = "; ".join(filter(None, failed_msgs)) or (
                        "One or more verifications failed"
                    )

                duration = round(time.perf_counter() - start_time, 2)
                self._print_test_progress(test, status, duration)

                # 6. Capture telemetry, run failure diagnosis, and return TestResult
                telemetry = session.get_telemetry() if hasattr(session, "get_telemetry") else {}
                c_logs = telemetry.get("console_logs", [])
                n_errs = telemetry.get("network_errors", [])
                p_state = None
                try:
                    if hasattr(session, "get_page_state") and getattr(session, "is_active", False):
                        p_state = await session.get_page_state()
                except Exception:
                    pass

                diag = None
                if status in ("fail", "error") and hasattr(self, "failure_analyzer"):
                    temp_res = _build_test_result(
                        test=test,
                        status=status,
                        duration=duration,
                        verifications=verifications,
                        error=test_error,
                        screenshot_path=screenshot_path,
                        jev_steps=jev_steps,
                        console_logs=c_logs,
                        network_errors=n_errs,
                    )
                    try:
                        diag = self.failure_analyzer.diagnose(test, temp_res, p_state)
                    except Exception as d_err:
                        logger.debug("Failure analyzer error: %s", d_err)

                return _build_test_result(
                    test=test,
                    status=status,
                    duration=duration,
                    verifications=verifications,
                    error=test_error,
                    screenshot_path=screenshot_path,
                    jev_steps=jev_steps,
                    console_logs=c_logs,
                    network_errors=n_errs,
                    diagnosis=diag,
                )

        except Exception as unhandled_err:  # noqa: BLE001
            duration = round(time.perf_counter() - start_time, 2)
            self._failed_test_ids.add(test_id)
            self._print_test_progress(test, "error", duration)
            temp_res = _build_test_result(
                test=test,
                status="error",
                duration=duration,
                verifications=verifications,
                error=f"Test runtime error: {unhandled_err}",
                screenshot_path=screenshot_path,
                jev_steps=jev_steps,
            )
            diag = self.failure_analyzer.diagnose(test, temp_res, None) if hasattr(self, "failure_analyzer") else None
            return _build_test_result(
                test=test,
                status="error",
                duration=duration,
                verifications=verifications,
                error=f"Test runtime error: {unhandled_err}",
                screenshot_path=screenshot_path,
                jev_steps=jev_steps,
                diagnosis=diag,
            )

    def _route_verifier(self, expectation_type: str):
        """Route to the correct verifier based on expectation type."""
        norm_type = expectation_type.strip().lower()
        if norm_type in ("dom", "selector", "element", "text") or norm_type.startswith("dom"):
            return self.dom_verifier
        elif norm_type in ("url", "path", "route", "redirect") or norm_type.startswith("url"):
            return self.url_verifier
        elif norm_type in ("semantic", "llm", "ai", "visual", "meaning") or norm_type.startswith(
            "semantic"
        ):
            return self.semantic_verifier
        else:
            raise ValueError(f"Unknown verification expectation type: '{expectation_type}'")

    def _postcondition_validation_error(self, test: TestCase) -> str | None:
        """Reject tests that cannot deterministically prove their requested outcome."""
        expectations = list(getattr(test, "expected", None) or [])
        if not expectations:
            return "Test requires at least one deterministic postcondition."

        deterministic = [
            expectation
            for expectation in expectations
            if self._get_expectation_type(expectation).strip().lower() in {"dom", "url"}
        ]
        if not deterministic:
            return "Interaction test requires at least one deterministic DOM or URL postcondition."

        meaningful = [
            expectation
            for expectation in deterministic
            if getattr(expectation, "selector", None) or getattr(expectation, "value", None)
        ]
        if not meaningful:
            return "Deterministic postcondition must specify an observable selector or value."

        actions = getattr(test, "actions", None)
        clicked_selectors = {
            action.selector
            for action in (actions or [])
            if getattr(action, "action", None) == "click"
        }
        if clicked_selectors and all(
            self._get_expectation_type(expectation).strip().lower() == "dom"
            and getattr(expectation, "selector", None) in clicked_selectors
            and getattr(expectation, "value", None) is None
            for expectation in meaningful
        ):
            return (
                "Postcondition only proves that the clicked control exists; "
                "it must verify an observable effect."
            )

        return None

    @staticmethod
    def _has_required_interaction(
        test: TestCase,
        steps: list[dict[str, Any]],
    ) -> bool:
        """Return whether the execution requested a state-changing browser interaction."""
        actions = getattr(test, "actions", None)
        if actions is not None:
            return any(
                getattr(action, "required", True)
                and getattr(action, "action", None) in {"click", "fill", "press"}
                for action in actions
            )
        return any(
            step.get("required", True)
            and step.get("status") == "completed"
            and step.get("action") in {"click", "fill", "press"}
            for step in steps
        )

    def _check_precondition_failure(self, test: TestCase) -> str | None:
        """Check if test depends on any previously failed or errored test."""
        # Check depends_on attribute
        depends_on = getattr(test, "depends_on", None)
        if depends_on:
            if isinstance(depends_on, (list, tuple, set)):
                for dep in depends_on:
                    if str(dep) in self._failed_test_ids:
                        return str(dep)
            elif isinstance(depends_on, str) and depends_on in self._failed_test_ids:
                return depends_on

        # Check preconditions attribute
        preconditions = getattr(test, "preconditions", None)
        if preconditions:
            if isinstance(preconditions, (list, tuple, set)):
                for pre in preconditions:
                    if isinstance(pre, str):
                        for failed_id in self._failed_test_ids:
                            if failed_id == pre or failed_id in pre:
                                return failed_id
                    elif isinstance(pre, dict):
                        dep_id = pre.get("test_id") or pre.get("depends_on") or pre.get("id")
                        if dep_id and str(dep_id) in self._failed_test_ids:
                            return str(dep_id)
                    elif hasattr(pre, "test_id") and str(pre.test_id) in self._failed_test_ids:
                        return str(pre.test_id)
            elif isinstance(preconditions, str):
                for failed_id in self._failed_test_ids:
                    if failed_id in preconditions:
                        return failed_id

        return None

    @asynccontextmanager
    async def _create_browser_session(self):
        """Context manager to ensure clean browser session lifecycle for each test."""
        session: BrowserSession
        try:
            session = BrowserSession(
                headless=self.headless,
                cdp_url=self.cdp_url,
                user_data_dir=self.user_data_dir,
                storage_state=self.storage_state,
                reuse_existing_context=self.reuse_existing_context,
                reuse_existing_page=self.reuse_existing_page,
            )
        except TypeError:
            try:
                session = BrowserSession(headless=self.headless)
            except TypeError:
                session = BrowserSession()  # type: ignore[call-arg]

        if hasattr(session, "__aenter__"):
            entered = await session.__aenter__()
            session = entered if entered is not None else session
        elif hasattr(session, "start") and callable(session.start):
            res = session.start()
            if inspect.isawaitable(res):
                await res
        elif hasattr(session, "open") and callable(session.open):
            res = session.open()
            if inspect.isawaitable(res):
                await res

        try:
            yield session
        finally:
            try:
                if hasattr(session, "__aexit__"):
                    await session.__aexit__(None, None, None)
                elif hasattr(session, "close") and callable(session.close):
                    res = session.close()
                    if inspect.isawaitable(res):
                        await res
                elif hasattr(session, "stop") and callable(session.stop):
                    res = session.stop()
                    if inspect.isawaitable(res):
                        await res
            except Exception as close_err:  # noqa: BLE001
                logger.warning("Error closing browser session: %s", close_err)

    async def _execute_jev(self, session: BrowserSession, test: TestCase) -> Any:
        """Run Jev with the test goal."""
        page = getattr(session, "page", None)
        run_fn = getattr(self.jev_runner, "run", None) or getattr(self.jev_runner, "execute", None)
        if run_fn is None:
            raise AttributeError("JevRunner has neither 'run' nor 'execute' method")

        sig = inspect.signature(run_fn)
        params = sig.parameters

        kwargs: dict[str, Any] = {}
        if "test" in params:
            kwargs["test"] = test
        if "browser_session" in params:
            kwargs["browser_session"] = session
        elif "session" in params:
            kwargs["session"] = session
        elif "page" in params and page is not None:
            kwargs["page"] = page

        if "goal" in params:
            kwargs["goal"] = (
                getattr(test, "goal", None)
                or getattr(test, "description", None)
                or getattr(test, "name", "")
            )
        if "url" in params:
            url_val = getattr(test, "start_url", None) or getattr(test, "url", None)
            if url_val:
                kwargs["url"] = url_val

        if kwargs:
            result = run_fn(**kwargs)
        else:
            param_names = list(params.keys())
            if len(param_names) == 1:
                result = run_fn(test)
            elif len(param_names) >= 2:
                result = run_fn(test, session)
            else:
                result = run_fn(test, session)

        if inspect.isawaitable(result):
            result = await result

        return result

    def _get_expectation_type(self, expectation: Any) -> str:
        """Extract or infer the expectation type from the verification expectation."""
        if isinstance(expectation, dict):
            t = (
                expectation.get("type")
                or expectation.get("expectation_type")
                or expectation.get("verifier")
            )
            if t:
                return str(t)
            if any(k in expectation for k in ("selector", "css", "xpath", "text", "dom")):
                return "dom"
            if any(k in expectation for k in ("url", "path", "route")):
                return "url"
            if any(k in expectation for k in ("prompt", "query", "semantic", "llm")):
                return "semantic"
        else:
            t = (
                getattr(expectation, "type", None)
                or getattr(expectation, "expectation_type", None)
                or getattr(expectation, "verifier", None)
            )
            if t:
                return str(t)
            if hasattr(expectation, "selector") or hasattr(expectation, "xpath"):
                return "dom"
            if hasattr(expectation, "url") or hasattr(expectation, "path"):
                return "url"
            if hasattr(expectation, "prompt") or hasattr(expectation, "query"):
                return "semantic"

        return "dom"

    async def _run_verifier(
        self,
        verifier: Any,
        session: BrowserSession,
        expectation: Any,
        expectation_type: str,
    ) -> VerificationResult:
        """Execute a verifier with the browser session and expectation."""
        page = getattr(session, "page", None)
        current_url = getattr(page, "url", "") if page else ""
        verify_fn = getattr(verifier, "verify", None) or getattr(verifier, "check", None)
        if verify_fn is None:
            raise AttributeError(
                f"Verifier {verifier.__class__.__name__} has neither 'verify' nor 'check' method"
            )

        sig = inspect.signature(verify_fn)
        params = sig.parameters

        kwargs: dict[str, Any] = {}
        if "expectation" in params:
            kwargs["expectation"] = expectation
        elif "expected" in params:
            kwargs["expected"] = expectation

        if "browser_session" in params:
            kwargs["browser_session"] = session
        elif "session" in params:
            kwargs["session"] = session
        elif "page" in params and page is not None:
            kwargs["page"] = page

        if "url" in params and "page" not in kwargs:
            kwargs["url"] = current_url
        elif "current_url" in params:
            kwargs["current_url"] = current_url

        if kwargs:
            res = verify_fn(**kwargs)
        else:
            param_names = list(params.keys())
            if len(param_names) == 1:
                res = verify_fn(expectation)
            elif len(param_names) >= 2:
                target = (
                    page if (page is not None and "page" in param_names[0].lower()) else session
                )
                res = verify_fn(target, expectation)
            else:
                target = page if page is not None else session
                res = verify_fn(target, expectation)

        if inspect.isawaitable(res):
            res = await res

        if isinstance(res, VerificationResult):
            return res

        passed = True
        message = "Verification passed"
        actual_value = None

        if isinstance(res, bool):
            passed = res
            message = "Verification passed" if res else "Verification failed"
        elif isinstance(res, dict):
            passed = res.get("passed", res.get("success", False))
            message = res.get("message", res.get("description", "Verification completed"))
            actual_value = res.get("actual_value", res.get("actual"))
        else:
            passed = bool(getattr(res, "passed", getattr(res, "success", True)))
            message = getattr(res, "message", getattr(res, "description", "Verification completed"))
            actual_value = getattr(res, "actual_value", getattr(res, "actual", None))

        return _build_verification_result(
            passed=passed,
            expectation_type=expectation_type,
            message=message,
            actual=actual_value,
            expectation=expectation,
        )

    async def _take_screenshot(
        self, session: BrowserSession, test_id: str, prefix: str
    ) -> str | None:
        """Capture and save a screenshot on test failure or error."""
        try:
            self.screenshots_dir.mkdir(parents=True, exist_ok=True)
            safe_id = "".join(c if c.isalnum() or c in ("-", "_") else "_" for c in str(test_id))
            timestamp = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
            filename = f"{safe_id}_{prefix}_{timestamp}.png"
            filepath = self.screenshots_dir / filename

            if hasattr(session, "screenshot") and callable(session.screenshot):
                res = session.screenshot(filepath)
                if inspect.isawaitable(res):
                    return await res
                return str(res)
            elif hasattr(session, "page") and session.page and hasattr(session.page, "screenshot"):
                res = session.page.screenshot(path=str(filepath))
                if inspect.isawaitable(res):
                    await res
                return str(filepath)
        except Exception as err:  # noqa: BLE001
            logger.warning("Failed to take screenshot for test %s: %s", test_id, err)
        return None

    def _print_test_progress(self, test: TestCase, status: str, duration: float) -> None:
        """Print real-time test progress using rich console."""
        test_id = getattr(test, "id", "") or getattr(test, "test_id", "") or "UNKNOWN"
        test_name = getattr(test, "name", "") or getattr(test, "test_name", "") or test_id

        norm_status = status.upper()
        status_styles = {
            "PASS": "[green bold]PASS[/green bold]",
            "FAIL": "[red bold]FAIL[/red bold]",
            "ERROR": "[magenta bold]ERROR[/magenta bold]",
            "SKIP": "[yellow bold]SKIP[/yellow bold]",
        }
        status_display = status_styles.get(norm_status, f"[bold]{norm_status}[/bold]")
        duration_display = "—" if norm_status == "SKIP" else f"{duration:.1f}s"

        self.console.print(
            f"  {test_id:<12} {test_name:<30} {status_display:<20} {duration_display:>6}"
        )
