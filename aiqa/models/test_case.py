"""AIQA Test Case and Execution Models.

This module defines Pydantic v2 schemas for test cases, expectations, execution
results, test suites, and run reports used by the AIQA testing framework.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Annotated, Any, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

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
    frame_selector: str | None = None


class FillAction(ActionDefinition):
    """Replace the value of a matching input element."""

    action: Literal["fill"]
    selector: NonEmptyStr
    value: str
    frame_selector: str | None = None


class PressAction(ActionDefinition):
    """Press a keyboard key, optionally while an element is focused."""

    action: Literal["press"]
    key: NonEmptyStr
    selector: NonEmptyStr | None = None
    frame_selector: str | None = None


class NavigateAction(ActionDefinition):
    """Navigate the active page to a URL."""

    action: Literal["navigate"]
    url: NonEmptyStr


class WaitAction(ActionDefinition):
    """Pause execution for a bounded amount of time."""

    action: Literal["wait"]
    timeout_ms: int = Field(default=1000, ge=1, le=10_000)


class UploadAction(ActionDefinition):
    """Set input file(s) on a file input element."""

    action: Literal["upload"]
    selector: NonEmptyStr
    file_paths: list[NonEmptyStr] = Field(..., min_length=1)
    frame_selector: str | None = None


class PopupAction(ActionDefinition):
    """Trigger a popup/OAuth window via click and optionally interact or switch to it."""

    action: Literal["popup"]
    trigger_selector: NonEmptyStr
    popup_click_selector: str | None = None
    wait_for_close: bool = True
    switch_to_popup: bool = False


BrowserAction = Annotated[
    ClickAction
    | FillAction
    | PressAction
    | NavigateAction
    | WaitAction
    | UploadAction
    | PopupAction,
    Field(discriminator="action"),
]


class ActionOutcome(BaseModel):
    """Auditable outcome emitted after attempting one browser action."""

    step: int = Field(..., ge=1)
    action: Literal["click", "fill", "press", "navigate", "wait", "upload", "popup"]
    status: Literal["completed", "failed"]
    details: str
    timestamp: float
    required: bool = True
    error: ActionError | None = None
    observed_url: str | None = None
    observed_title: str | None = None
    state_delta: str | None = None


class FixtureSpec(BaseModel):
    """Explicit setup or teardown fixture specification via HTTP API."""

    name: NonEmptyStr
    method: Literal["POST", "PUT", "DELETE", "GET"] = "POST"
    url: NonEmptyStr
    headers: dict[str, str] = Field(default_factory=dict)
    payload: dict[str, Any] | None = None
    entity_id_field: str = "id"
    teardown_url_template: str | None = None
    teardown_method: Literal["DELETE", "POST"] = "DELETE"


class CreatedEntityRecord(BaseModel):
    """Audit record for test data created by a setup fixture."""

    fixture_name: str
    entity_id: str
    resource_url: str
    cleaned_up: bool = False


class Expectation(BaseModel):
    """An individual expectation or assertion to verify after test execution.

    Attributes:
        type: Verification type (dom, url, api, visual, semantic, download, a11y).
        description: Human-readable description of what to verify.
        selector: Optional CSS selector for DOM-based checks.
        value: Optional expected value (e.g. text content, URL string, status, filename).
        frame_selector: Optional iframe selector to scope DOM checks inside an iframe.
    """

    type: Literal["dom", "url", "api", "visual", "semantic", "download", "a11y", "business_rule"] = Field(
        ...,
        description="Verification type (dom, url, api, visual, semantic, download, a11y, business_rule)",
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
        description="Expected value (e.g. text content, URL string, status code, filename)",
    )
    frame_selector: str | None = Field(
        default=None,
        description="Optional CSS selector for an iframe containing the target element",
    )
    oracle: str | None = Field(
        default=None,
        description="Explicit business rule or expected criterion required to judge correctness",
    )
    inconclusive_if_missing_oracle: bool = Field(
        default=False,
        description="If True and neither value nor oracle is provided, mark verification inconclusive instead of pass",
    )


class VerificationResult(BaseModel):
    """The outcome of evaluating an individual Expectation.

    Attributes:
        expectation: The expectation that was evaluated.
        passed: True if the expectation was satisfied, False otherwise.
        actual_value: The observed actual value during verification, if applicable.
        message: Diagnostic explanation or verification details.
        inconclusive: True when the expectation cannot be judged without a business oracle.
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
    inconclusive: bool = Field(
        default=False,
        description="True when verification cannot be judged without an explicit business-rule oracle",
    )


class TestCase(BaseModel):
    """A single test case definition for autonomous browser execution."""

    __test__ = False

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
    depends_on: list[str] = Field(
        default_factory=list,
        description="Explicit test IDs that must execute before this test",
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
        ge=1,
        le=600,
        description="Maximum execution timeout in seconds",
    )
    max_steps: int = Field(
        default=10,
        ge=1,
        le=50,
        description="Maximum steps allowed in dynamic observe-decide-act loop",
    )
    max_cost_usd: float = Field(
        default=0.50,
        ge=0.0,
        le=10.0,
        description="Maximum LLM cost budget in USD for dynamic execution",
    )
    role: str | None = Field(
        default=None,
        description="Optional role account name for authenticated execution",
    )
    viewport: dict[str, int] | None = Field(
        default=None,
        description="Optional per-test viewport override {'width': int, 'height': int}",
    )
    is_mobile: bool = Field(
        default=False,
        description="Enable mobile viewport/touch emulation",
    )
    user_agent: str | None = Field(
        default=None,
        description="Optional custom User-Agent string",
    )
    setup_fixtures: list[FixtureSpec] = Field(
        default_factory=list,
        description="Explicit API/DB setup fixtures executed before the test",
    )
    teardown_fixtures: list[FixtureSpec] = Field(
        default_factory=list,
        description="Explicit API/DB teardown fixtures executed after the test",
    )


class FailureDiagnosis(BaseModel):
    """Automated root-cause analysis for a failed or errored test case."""

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


class AttemptRecord(BaseModel):
    """Record of an individual execution attempt for a test case."""

    attempt: int = Field(..., ge=1, description="1-indexed attempt number")
    status: Literal["pass", "fail", "error", "skip"] = Field(
        ...,
        description="Outcome of this attempt",
    )
    duration_seconds: float = Field(
        ...,
        description="Duration of this attempt in seconds",
    )
    error_message: str | None = Field(
        default=None,
        description="Error or failure message on this attempt",
    )
    failure_category: str | None = Field(
        default=None,
        description=(
            "Classification of failure ('transient_infra', 'deterministic_assertion', "
            "'policy_violation', 'action_failure')"
        ),
    )


class TestResult(BaseModel):
    """Execution result for a single TestCase."""

    __test__ = False

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
    attempts: list[AttemptRecord] = Field(
        default_factory=list,
        description="All execution attempts for this test case",
    )
    flaky: bool = Field(
        default=False,
        description="True if test failed transiently on an initial attempt and passed on retry",
    )
    failure_category: str | None = Field(
        default=None,
        description="Classification of failure if failed or errored",
    )
    created_entities: list[CreatedEntityRecord] = Field(
        default_factory=list,
        description="Entities created by setup fixtures and their cleanup status",
    )
    cleanup_errors: list[str] = Field(
        default_factory=list,
        description="Teardown fixture errors recorded separately from test assertions",
    )
    inconclusive: bool = Field(
        default=False,
        description="True if one or more business rules could not be judged without an explicit oracle",
    )
    inconclusive_reasons: list[str] = Field(
        default_factory=list,
        description="Explicit reasons why business rules were marked inconclusive",
    )


class TestSuite(BaseModel):
    """A collection of test cases configured against a base URL."""

    __test__ = False

    schema_version: str = Field(
        default="1.0",
        description="Schema version for test suite format",
    )
    name: str = Field(
        ...,
        description="Name of the test suite",
    )
    base_url: str = Field(
        ...,
        description="Base URL against which tests will execute",
    )
    owner: str = Field(
        default="qa-platform-team",
        description="Named owner responsible for suite failures and auth artifacts",
    )
    tests: list[TestCase] = Field(
        ...,
        description="List of test cases in the suite",
    )
    planning_notes: list[str] = Field(
        default_factory=list,
        description="Explanations of omitted candidate interactions or planning observations",
    )
    allowed_origins: list[str] = Field(
        default_factory=list,
        description="Additional allowed origins for navigation",
    )
    allow_cross_origin: bool = Field(
        default=False,
        description="Whether cross-origin navigation is permitted",
    )
    roles: dict[str, dict[str, Any]] = Field(
        default_factory=dict,
        description="Per-role authentication and storage-state configuration",
    )
    setup_fixtures: list[FixtureSpec] = Field(
        default_factory=list,
        description="Suite-level setup fixtures",
    )
    teardown_fixtures: list[FixtureSpec] = Field(
        default_factory=list,
        description="Suite-level teardown fixtures",
    )

    @model_validator(mode="after")
    def validate_unique_test_ids(self) -> Self:
        seen = set()
        duplicates = []
        for test in self.tests:
            if test.id in seen:
                duplicates.append(test.id)
            seen.add(test.id)
        if duplicates:
            raise ValueError(f"Duplicate test case IDs found: {', '.join(duplicates)}")
        return self


class RunSummary(BaseModel):
    """Aggregated summary metrics for a test run."""

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
    inconclusive: int = Field(
        default=0,
        description="Count of tests marked inconclusive due to missing business-rule oracle",
    )
    flaky: int = Field(
        default=0,
        description="Count of tests that passed only after transient retry",
    )
    cleanup_failures: int = Field(
        default=0,
        description="Count of tests where fixture teardown encountered errors",
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
        """Calculate summary statistics from a list of test results."""
        total = len(results)
        passed = sum(1 for r in results if r.status == "pass")
        failed = sum(1 for r in results if r.status == "fail")
        errors = sum(1 for r in results if r.status == "error")
        skipped = sum(1 for r in results if r.status == "skip")
        inconclusive = sum(1 for r in results if getattr(r, "inconclusive", False))
        flaky = sum(1 for r in results if getattr(r, "flaky", False))
        cleanup_failures = sum(1 for r in results if getattr(r, "cleanup_errors", None))
        pass_rate = (passed / total) if total > 0 else 0.0

        if duration_seconds <= 0.0 and results:
            duration_seconds = sum(r.duration_seconds for r in results)

        return cls(
            total=total,
            passed=passed,
            failed=failed,
            errors=errors,
            skipped=skipped,
            inconclusive=inconclusive,
            flaky=flaky,
            cleanup_failures=cleanup_failures,
            duration_seconds=duration_seconds,
            pass_rate=pass_rate,
        )


class TestRunReport(BaseModel):
    """Complete execution report for a test suite run."""

    __test__ = False

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
    owner: str = Field(
        default="qa-platform-team",
        description="Named owner responsible for suite failures",
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
        owner: str = "qa-platform-team",
    ) -> TestRunReport:
        """Create a TestRunReport with automatically computed summary metrics."""
        duration = max(0.0, (finished_at - started_at).total_seconds())
        summary = RunSummary.from_results(results, duration_seconds=duration)
        return cls(
            run_id=run_id,
            suite_name=suite_name,
            base_url=base_url,
            owner=owner,
            started_at=started_at,
            finished_at=finished_at,
            results=results,
            summary=summary,
        )

    @classmethod
    def aggregate(
        cls,
        reports: list[TestRunReport],
        run_id: str | None = None,
    ) -> TestRunReport:
        """Aggregate multiple shard TestRunReports into a single unified report.

        Validates that each test_id appears at most once across shards.
        """
        if not reports:
            raise ValueError("Cannot aggregate an empty list of TestRunReports")

        seen_ids: set[str] = set()
        combined_results: list[TestResult] = []
        for rep in reports:
            for res in rep.results:
                if res.test_id in seen_ids:
                    raise ValueError(
                        f"Duplicate test_id '{res.test_id}' found across aggregated shard reports"
                    )
                seen_ids.add(res.test_id)
                combined_results.append(res)

        started_at = min(r.started_at for r in reports)
        finished_at = max(r.finished_at for r in reports)
        wall_duration = max(
            0.0,
            (finished_at - started_at).total_seconds(),
            max((r.summary.duration_seconds for r in reports), default=0.0),
        )
        summary = RunSummary.from_results(combined_results, duration_seconds=wall_duration)
        first = reports[0]
        return cls(
            run_id=run_id or f"agg-{first.run_id}",
            suite_name=first.suite_name,
            base_url=first.base_url,
            owner=getattr(first, "owner", "qa-platform-team"),
            started_at=started_at,
            finished_at=finished_at,
            results=combined_results,
            summary=summary,
        )


def load_test_suite(path: Path | str) -> TestSuite:
    """Load and validate a TestSuite from a JSON file."""
    file_path = Path(path)
    if not file_path.exists():
        raise FileNotFoundError(f"Test suite file not found: {file_path}")
    if not file_path.is_file():
        raise ValueError(f"Specified path is not a regular file: {file_path}")

    content = file_path.read_text(encoding="utf-8")
    return TestSuite.model_validate_json(content)


__all__ = [
    "AttemptRecord",
    "BrowserAction",
    "ClickAction",
    "CreatedEntityRecord",
    "Expectation",
    "FailureDiagnosis",
    "FillAction",
    "FixtureSpec",
    "NavigateAction",
    "PopupAction",
    "PressAction",
    "RunSummary",
    "TestCase",
    "TestResult",
    "TestRunReport",
    "TestSuite",
    "UploadAction",
    "VerificationResult",
    "WaitAction",
    "load_test_suite",
]
