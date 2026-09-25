"""AIQA Test Case and Execution Models.

This module defines Pydantic v2 schemas for test cases, expectations, execution
results, test suites, and run reports used by the AIQA testing framework.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

NonEmptyStr = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]


class ActionError(BaseModel):
    """Machine-readable details for an action planning or execution failure."""

    code: NonEmptyStr
    message: NonEmptyStr
    details: dict[str, Any] = Field(default_factory=dict)


class ActionDefinition(BaseModel):
    """Fields shared by every executable browser action."""

    model_config = ConfigDict(extra="forbid")

    description: str = ""
    required: bool = True


class ClickAction(ActionDefinition):
    """Click the first element matching a CSS selector."""

    action: Literal["click"]
    selector: NonEmptyStr


class FillAction(ActionDefinition):
    """Replace the value of a matching input element."""

    action: Literal["fill"]
    selector: NonEmptyStr
    value: str


class PressAction(ActionDefinition):
    """Press a keyboard key, optionally while an element is focused."""

    action: Literal["press"]
    key: NonEmptyStr
    selector: NonEmptyStr | None = None


class NavigateAction(ActionDefinition):
    """Navigate the active page to a URL."""

    action: Literal["navigate"]
    url: NonEmptyStr


class WaitAction(ActionDefinition):
    """Pause execution for a bounded amount of time."""

    action: Literal["wait"]
    timeout_ms: int = Field(default=1000, ge=1, le=10_000)


BrowserAction = Annotated[
    ClickAction | FillAction | PressAction | NavigateAction | WaitAction,
    Field(discriminator="action"),
]


class ActionOutcome(BaseModel):
    """Auditable outcome emitted after attempting one browser action."""

    step: int = Field(..., ge=1)
    action: Literal["click", "fill", "press", "navigate", "wait"]
    status: Literal["completed", "failed"]
    details: str
    timestamp: float
    required: bool = True
    error: ActionError | None = None


class Expectation(BaseModel):
    """An individual expectation or assertion to verify after test execution.

    Attributes:
        type: Verification type (dom, url, api, visual, semantic).
        description: Human-readable description of what to verify.
        selector: Optional CSS selector for DOM-based checks.
        value: Optional expected value (e.g. text content, URL string, status).
    """

    type: Literal["dom", "url", "api", "visual", "semantic"] = Field(
        ...,
        description="Verification type (dom, url, api, visual, semantic)",
    )
    description: str = Field(
        ...,
        description="Human-readable description of what to verify",
    )
    selector: str | None = Field(
        default=None,
        description="CSS selector for DOM-based checks",
    )
    value: str | None = Field(
        default=None,
        description="Expected value (e.g. text content, URL string, status code)",
    )


class VerificationResult(BaseModel):
    """The outcome of evaluating an individual Expectation.

    Attributes:
        expectation: The expectation that was evaluated.
        passed: True if the expectation was satisfied, False otherwise.
        actual_value: The observed actual value during verification, if applicable.
        message: Diagnostic explanation or verification details.
    """

    expectation: Expectation = Field(
        ...,
        description="The expectation that was evaluated",
    )
    passed: bool = Field(
        ...,
        description="Whether the expectation was satisfied",
    )
    actual_value: str | None = Field(
        default=None,
        description="Observed actual value during verification",
    )
    message: str = Field(
        ...,
        description="Diagnostic explanation or verification details",
    )


class TestCase(BaseModel):
    """A single test case definition for autonomous browser execution.

    Attributes:
        id: Unique identifier for the test case (e.g. 'CART-001').
        name: Human-readable name of the test.
        start_url: Relative or absolute URL where execution begins.
        preconditions: Natural language preconditions before test execution.
        goal: Natural language goal instructed to the Jev browser agent.
        expected: List of expectations to verify after execution completes.
        cleanup: Cleanup actions to perform after execution.
        tags: Categorization tags (e.g. ['cart', 'smoke']).
        timeout: Maximum execution timeout in seconds (defaults to 60).
    """

    id: str = Field(
        ...,
        description="Unique identifier for the test case (e.g. 'CART-001')",
    )
    name: str = Field(
        ...,
        description="Human-readable name of the test",
    )
    start_url: str = Field(
        ...,
        description="Relative or absolute URL where execution begins",
    )
    preconditions: list[str] = Field(
        default_factory=list,
        description="Natural language preconditions before test execution",
    )
    goal: str = Field(
        default="",
        description="Natural language goal instructed to the Jev browser agent",
    )
    actions: list[BrowserAction] | None = Field(
        default=None,
        description=(
            "Validated executable action plan. Omission preserves legacy goal-based suites; "
            "an explicit empty list is invalid at execution time."
        ),
    )
    expected: list[Expectation] = Field(
        ...,
        description="List of expectations to verify after execution completes",
    )
    cleanup: list[str] = Field(
        default_factory=list,
        description="Cleanup actions to perform after execution",
    )
    tags: list[str] = Field(
        default_factory=list,
        description="Categorization tags (e.g. ['cart', 'smoke'])",
    )
    timeout: int = Field(
        default=60,
        description="Maximum execution timeout in seconds",
    )


class FailureDiagnosis(BaseModel):
    """Automated root-cause analysis for a failed or errored test case.

    Attributes:
        summary: High-level diagnosis summary.
        likely_cause: Primary root cause identified (network, selector, JS crash, etc.).
        evidence: Concrete evidence points supporting the diagnosis.
        remediation: Actionable guidance on how to fix the issue.
        severity: Diagnosis severity ('critical', 'high', 'medium', 'low').
    """

    summary: str = Field(
        ...,
        description="High-level diagnosis summary",
    )
    likely_cause: str = Field(
        ...,
        description="Primary root cause identified (network, selector, JS crash, etc.)",
    )
    evidence: list[str] = Field(
        default_factory=list,
        description="Concrete evidence points supporting the diagnosis",
    )
    remediation: str = Field(
        ...,
        description="Actionable guidance on how to fix the issue",
    )
    severity: Literal["critical", "high", "medium", "low"] = Field(
        default="medium",
        description="Diagnosis severity (critical, high, medium, low)",
    )


class TestResult(BaseModel):
    """Execution result for a single TestCase.

    Attributes:
        test_id: Identifier of the executed test case.
        status: Test outcome ('pass', 'fail', 'error', 'skip').
        duration_seconds: Execution duration in seconds.
        jev_steps: Raw Jev execution trace events.
        verification_results: Results of evaluating each expectation.
        error_message: Optional error message if execution failed or errored.
        screenshot_path: Optional path to final or failure screenshot.
        timestamp: Timestamp when the test completed.
        console_logs: Browser console messages (errors, warnings) captured during execution.
        network_errors: Network request failures (4xx, 5xx, or aborted requests) captured.
        diagnosis: Automated root-cause failure analysis if the test failed or errored.
    """

    test_id: str = Field(
        ...,
        description="Identifier of the executed test case",
    )
    status: Literal["pass", "fail", "error", "skip"] = Field(
        ...,
        description="Test outcome status",
    )
    duration_seconds: float = Field(
        ...,
        description="Execution duration in seconds",
    )
    jev_steps: list[dict[str, Any]] = Field(
        ...,
        description="Raw Jev execution trace events",
    )
    verification_results: list[VerificationResult] = Field(
        ...,
        description="Results of evaluating each expectation",
    )
    error_message: str | None = Field(
        default=None,
        description="Error message if execution failed or errored",
    )
    screenshot_path: str | None = Field(
        default=None,
        description="Path to final or failure screenshot",
    )
    timestamp: datetime = Field(
        ...,
        description="Timestamp when the test execution completed",
    )
    console_logs: list[dict[str, Any]] = Field(
        default_factory=list,
        description="Browser console messages (errors, warnings) captured during execution",
    )
    network_errors: list[dict[str, Any]] = Field(
        default_factory=list,
        description="Network request failures (4xx, 5xx, or aborted requests) captured during execution",
    )
    diagnosis: FailureDiagnosis | None = Field(
        default=None,
        description="Automated root-cause failure analysis if the test failed or errored",
    )


class TestSuite(BaseModel):
    """A collection of test cases configured against a base URL.

    Attributes:
        name: Name of the test suite.
        base_url: Base URL against which tests will execute.
        tests: Ordered list of test cases in the suite.
    """

    name: str = Field(
        ...,
        description="Name of the test suite",
    )
    base_url: str = Field(
        ...,
        description="Base URL against which tests will execute",
    )
    tests: list[TestCase] = Field(
        ...,
        description="List of test cases in the suite",
    )


class RunSummary(BaseModel):
    """Aggregated summary metrics for a test run.

    Attributes:
        total: Total number of tests executed or scheduled.
        passed: Count of tests that passed all verifications.
        failed: Count of tests that failed one or more verifications.
        errors: Count of tests that encountered runtime errors.
        skipped: Count of tests that were skipped.
        duration_seconds: Total duration of the test run in seconds.
        pass_rate: Proportion of passed tests (0.0 to 1.0).
    """

    total: int = Field(
        ...,
        description="Total number of tests executed or scheduled",
    )
    passed: int = Field(
        ...,
        description="Count of tests that passed all verifications",
    )
    failed: int = Field(
        ...,
        description="Count of tests that failed verifications",
    )
    errors: int = Field(
        ...,
        description="Count of tests that encountered runtime errors",
    )
    skipped: int = Field(
        ...,
        description="Count of tests that were skipped",
    )
    duration_seconds: float = Field(
        ...,
        description="Total duration of the test run in seconds",
    )
    pass_rate: float = Field(
        ...,
        description="Proportion of passed tests (0.0 - 1.0)",
    )

    @classmethod
    def from_results(
        cls,
        results: list[TestResult],
        duration_seconds: float = 0.0,
    ) -> RunSummary:
        """Calculate summary statistics from a list of test results.

        Args:
            results: The list of executed TestResult instances.
            duration_seconds: Total duration in seconds. If 0.0 or negative,
                the sum of individual result durations is used.

        Returns:
            Computed RunSummary instance.
        """
        total = len(results)
        passed = sum(1 for r in results if r.status == "pass")
        failed = sum(1 for r in results if r.status == "fail")
        errors = sum(1 for r in results if r.status == "error")
        skipped = sum(1 for r in results if r.status == "skip")
        pass_rate = (passed / total) if total > 0 else 0.0

        if duration_seconds <= 0.0 and results:
            duration_seconds = sum(r.duration_seconds for r in results)

        return cls(
            total=total,
            passed=passed,
            failed=failed,
            errors=errors,
            skipped=skipped,
            duration_seconds=duration_seconds,
            pass_rate=pass_rate,
        )


class TestRunReport(BaseModel):
    """Complete execution report for a test suite run.

    Attributes:
        run_id: Unique identifier for this test run.
        suite_name: Name of the executed test suite.
        base_url: Base URL targeted during this run.
        started_at: Timestamp when execution started.
        finished_at: Timestamp when execution finished.
        results: Detailed results for each test case.
        summary: Aggregated metrics and pass rate.
    """

    run_id: str = Field(
        ...,
        description="Unique identifier for this test run",
    )
    suite_name: str = Field(
        ...,
        description="Name of the executed test suite",
    )
    base_url: str = Field(
        ...,
        description="Base URL targeted during this run",
    )
    started_at: datetime = Field(
        ...,
        description="Timestamp when execution started",
    )
    finished_at: datetime = Field(
        ...,
        description="Timestamp when execution finished",
    )
    results: list[TestResult] = Field(
        ...,
        description="Detailed results for each test case",
    )
    summary: RunSummary = Field(
        ...,
        description="Aggregated metrics and pass rate",
    )

    @classmethod
    def create(
        cls,
        run_id: str,
        suite_name: str,
        base_url: str,
        started_at: datetime,
        finished_at: datetime,
        results: list[TestResult],
    ) -> TestRunReport:
        """Create a TestRunReport with automatically computed summary metrics.

        Args:
            run_id: Unique run identifier.
            suite_name: Name of the test suite.
            base_url: Base URL targeted.
            started_at: Timestamp when the run started.
            finished_at: Timestamp when the run ended.
            results: List of test results.

        Returns:
            TestRunReport with populated summary.
        """
        duration = max(0.0, (finished_at - started_at).total_seconds())
        summary = RunSummary.from_results(results, duration_seconds=duration)
        return cls(
            run_id=run_id,
            suite_name=suite_name,
            base_url=base_url,
            started_at=started_at,
            finished_at=finished_at,
            results=results,
            summary=summary,
        )


def load_test_suite(path: Path | str) -> TestSuite:
    """Load and validate a TestSuite from a JSON file.

    Args:
        path: Path to the JSON test suite file (Path object or string).

    Returns:
        Validated TestSuite instance.

    Raises:
        FileNotFoundError: If the file does not exist.
        ValueError: If the path is not a file or content fails schema validation.
    """
    file_path = Path(path)
    if not file_path.exists():
        raise FileNotFoundError(f"Test suite file not found: {file_path}")
    if not file_path.is_file():
        raise ValueError(f"Specified path is not a regular file: {file_path}")

    content = file_path.read_text(encoding="utf-8")
    return TestSuite.model_validate_json(content)


__all__ = [
    "Expectation",
    "FailureDiagnosis",
    "RunSummary",
    "TestCase",
    "TestResult",
    "TestRunReport",
    "TestSuite",
    "VerificationResult",
    "load_test_suite",
]
