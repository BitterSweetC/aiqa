"""
aiqa.orchestrator.runner
~~~~~~~~~~~~~~~~~~~~~~~~

The central test execution orchestrator for AIQA.
Coordinates test sessions, executes Jev goals, verifies outcomes,
tracks execution metrics, and produces structured test run reports.
"""

from __future__ import annotations

import asyncio
import heapq
import inspect
import logging
import re
import time
from collections.abc import Sequence
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urljoin
from uuid import uuid4

from rich.console import Console
from rich.table import Table

from aiqa.analyzer import FailureAnalyzer
from aiqa.auth.workflow import (
    resolve_role_storage_state,
    resolve_test_case_secrets,
    validate_storage_state,
)
from aiqa.executor.browser_session import BrowserSession
from aiqa.executor.jev_runner import JevRunner
from aiqa.fixtures.lifecycle import FixtureLifecycleManager
from aiqa.models.test_case import (
    AttemptRecord,
    CreatedEntityRecord,
    Expectation,
    FailureDiagnosis,
    RunSummary,
    TestCase,
    TestResult,
    TestRunReport,
    TestSuite,
    VerificationResult,
)
from aiqa.security.policy import extract_origin, is_origin_allowed, is_safe_url_scheme
from aiqa.security.redaction import redact_data, redact_text
from aiqa.verifier.accessibility import AccessibilityVerifier
from aiqa.verifier.dom import DomVerifier
from aiqa.verifier.download import DownloadVerifier
from aiqa.verifier.semantic import SemanticVerifier
from aiqa.verifier.url import UrlVerifier

logger = logging.getLogger(__name__)

_TRANSIENT_INFRA_MARKERS = (
    "net::err_connection_reset",
    "net::err_connection_refused",
    "net::err_connection_aborted",
    "net::err_connection_closed",
    "net::err_timed_out",
    "net::err_network_changed",
    "net::err_socket_not_connected",
    "net::err_name_not_resolved",
    "econnreset",
    "econnrefused",
    "etimedout",
    "service unavailable",
    "bad gateway",
    "gateway timeout",
    "http 502",
    "http 503",
    "http 504",
    "status 502",
    "status 503",
    "status 504",
    "target closed",
    "browser has been closed",
    "navigation timeout",
    "transient infrastructure",
    "transient_infra",
)


def parse_shard_spec(shard: str | tuple[int, int] | None) -> tuple[int, int] | None:
    """Parse and validate a 1-indexed shard specification like '1/4' or (1, 4)."""
    if shard is None:
        return None
    if isinstance(shard, tuple):
        if len(shard) != 2:
            raise ValueError(f"Invalid shard tuple: {shard}")
        idx, total = int(shard[0]), int(shard[1])
    else:
        raw = str(shard).strip()
        if not raw:
            return None
        parts = raw.split("/")
        if len(parts) != 2 or not parts[0].isdigit() or not parts[1].isdigit():
            raise ValueError(
                f"Invalid shard specification '{shard}'. Expected format 'INDEX/TOTAL' (e.g. '1/4')."
            )
        idx, total = int(parts[0]), int(parts[1])

    if total < 1 or idx < 1 or idx > total:
        raise ValueError(
            f"Invalid shard specification '{idx}/{total}': require 1 <= INDEX <= TOTAL."
        )
    return idx, total


def _string_references_test_id(text: str, candidate_id: str) -> bool:
    """Return whether `text` references `candidate_id` on token boundaries (avoiding substring collisions)."""
    if not text or not candidate_id:
        return False
    if text.strip() == candidate_id:
        return True
    pattern = rf"(?<![A-Za-z0-9_-]){re.escape(candidate_id)}(?![A-Za-z0-9_-])"
    return bool(re.search(pattern, text))


def _extract_test_dependencies(test: TestCase, candidate_ids: Sequence[str]) -> list[str]:
    """Extract all test IDs that `test` depends on via `depends_on` or `preconditions`."""
    deps: list[str] = []
    test_id = getattr(test, "id", "")
    id_set = set(candidate_ids)

    for dep in getattr(test, "depends_on", None) or []:
        dep_str = str(dep).strip()
        if dep_str and dep_str != test_id and dep_str in id_set and dep_str not in deps:
            deps.append(dep_str)

    for pre in getattr(test, "preconditions", None) or []:
        if isinstance(pre, str):
            for cid in candidate_ids:
                if cid != test_id and _string_references_test_id(pre, cid) and cid not in deps:
                    deps.append(cid)
        elif isinstance(pre, dict):
            dep_id = pre.get("test_id") or pre.get("depends_on") or pre.get("id")
            if (
                dep_id
                and str(dep_id) != test_id
                and str(dep_id) in id_set
                and str(dep_id) not in deps
            ):
                deps.append(str(dep_id))
        elif hasattr(pre, "test_id"):
            dep_id = str(pre.test_id)
            if dep_id != test_id and dep_id in id_set and dep_id not in deps:
                deps.append(dep_id)

    return deps


def _group_tests_by_dependency(tests: Sequence[TestCase]) -> list[list[TestCase]]:
    """Group tests into connected dependency DAG components and topologically order each group.

    Unlike single-parent grouping, if test C depends on both A and B, A, B, and C are merged
    into a single group `[A, B, C]` and ordered topologically so all prerequisites execute
    before their dependents.
    """
    if not tests:
        return []

    all_ids = [t.id for t in tests]
    id_to_index = {t.id: idx for idx, t in enumerate(tests)}
    id_to_test = {t.id: t for t in tests}
    deps_by_id: dict[str, list[str]] = {
        t.id: _extract_test_dependencies(t, all_ids) for t in tests
    }

    parent: dict[str, str] = {tid: tid for tid in all_ids}

    def _find(x: str) -> str:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def _union(a: str, b: str) -> None:
        ra, rb = _find(a), _find(b)
        if ra == rb:
            return
        if id_to_index[ra] <= id_to_index[rb]:
            parent[rb] = ra
        else:
            parent[ra] = rb

    for tid, dep_ids in deps_by_id.items():
        for dep_id in dep_ids:
            _union(dep_id, tid)

    component_members: dict[str, list[str]] = {}
    for tid in all_ids:
        root = _find(tid)
        component_members.setdefault(root, []).append(tid)

    sorted_roots = sorted(
        component_members.keys(),
        key=lambda r: min(id_to_index[m] for m in component_members[r]),
    )

    groups: list[list[TestCase]] = []
    for root in sorted_roots:
        members = component_members[root]
        member_set = set(members)
        indegree: dict[str, int] = {m: 0 for m in members}
        dependents: dict[str, list[str]] = {m: [] for m in members}

        for m in members:
            for dep_id in deps_by_id[m]:
                if dep_id in member_set:
                    dependents[dep_id].append(m)
                    indegree[m] += 1

        ready_heap: list[tuple[int, int, str]] = [
            (0 if dependents[m] else 1, id_to_index[m], m)
            for m in members
            if indegree[m] == 0
        ]
        heapq.heapify(ready_heap)

        ordered_ids: list[str] = []
        seen: set[str] = set()
        while len(ordered_ids) < len(members):
            while ready_heap:
                _, _, current_id = heapq.heappop(ready_heap)
                if current_id in seen:
                    continue
                seen.add(current_id)
                ordered_ids.append(current_id)
                for nxt in dependents[current_id]:
                    if nxt in seen:
                        continue
                    indegree[nxt] -= 1
                    if indegree[nxt] <= 0:
                        heapq.heappush(
                            ready_heap,
                            (0 if dependents[nxt] else 1, id_to_index[nxt], nxt),
                        )

            if len(ordered_ids) < len(members):
                remaining = [m for m in members if m not in seen]
                cycle_entry = min(
                    remaining,
                    key=lambda m: (
                        indegree[m],
                        0 if any(d not in seen for d in dependents[m]) else 1,
                        0 if dependents[m] else 1,
                        id_to_index[m],
                    ),
                )
                indegree[cycle_entry] = 0
                heapq.heappush(
                    ready_heap,
                    (0 if dependents[cycle_entry] else 1, id_to_index[cycle_entry], cycle_entry),
                )

        groups.append([id_to_test[tid] for tid in ordered_ids])

    return groups


def select_tests(
    suite: TestSuite,
    select_ids: Sequence[str] | None = None,
    tags: Sequence[str] | None = None,
    shard: str | tuple[int, int] | None = None,
    include_dependencies: bool = False,
) -> TestSuite:
    """Deterministically filter and shard a TestSuite while preserving dependency chains."""
    id_set = {s.strip() for s in (select_ids or []) if s and s.strip()}
    tag_set = {t.strip() for t in (tags or []) if t and t.strip()}
    parsed_shard = parse_shard_spec(shard)

    filtered: list[TestCase] = []
    for test in suite.tests:
        if id_set and test.id not in id_set:
            continue
        if tag_set and not (set(test.tags or []) & tag_set):
            continue
        filtered.append(test)

    if include_dependencies and (id_set or tag_set) and filtered:
        all_ids = [t.id for t in suite.tests]
        id_to_test = {t.id: t for t in suite.tests}
        needed_ids = {t.id for t in filtered}
        queue = list(needed_ids)
        while queue:
            curr_id = queue.pop(0)
            curr_test = id_to_test.get(curr_id)
            if curr_test is not None:
                for dep_id in _extract_test_dependencies(curr_test, all_ids):
                    if dep_id not in needed_ids:
                        needed_ids.add(dep_id)
                        queue.append(dep_id)
        filtered = [t for t in suite.tests if t.id in needed_ids]

    if parsed_shard is not None:
        shard_idx, total_shards = parsed_shard
        groups = _group_tests_by_dependency(filtered)
        sharded: list[TestCase] = []
        for group_i, group in enumerate(groups):
            if (group_i % total_shards) == (shard_idx - 1):
                sharded.extend(group)
        filtered = sharded

    return suite.model_copy(update={"tests": filtered})


def classify_failure(result: TestResult) -> str | None:
    """Classify a test result failure into a deterministic or transient category.

    Returns:
        None if passed or skipped; otherwise one of:
        - 'inconclusive_business_rule'
        - 'policy_violation'
        - 'deterministic_assertion'
        - 'transient_infra'
        - 'action_failure'
    """
    status = getattr(result, "status", "").lower()
    if status in ("pass", "skip"):
        return None

    if getattr(result, "inconclusive", False):
        return "inconclusive_business_rule"

    err = (getattr(result, "error_message", None) or "").lower()
    if "violates origin" in err or "unsafe url scheme" in err or "unsafe or invalid url" in err:
        return "policy_violation"

    if status == "fail":
        return "deterministic_assertion"

    # For status == 'error', check if the error is a transient infrastructure failure
    if any(marker in err for marker in _TRANSIENT_INFRA_MARKERS):
        return "transient_infra"

    for net_err in getattr(result, "network_errors", None) or []:
        if isinstance(net_err, dict):
            net_status = net_err.get("status")
            net_text = str(net_err.get("failure") or net_err.get("error") or "").lower()
            if net_status in (502, 503, 504) or any(
                m in net_text for m in _TRANSIENT_INFRA_MARKERS
            ):
                return "transient_infra"

    return "action_failure"


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
    attempts: list[AttemptRecord] | None = None,
    flaky: bool = False,
    failure_category: str | None = None,
    created_entities: list[CreatedEntityRecord] | None = None,
    cleanup_errors: list[str] | None = None,
    inconclusive: bool = False,
    inconclusive_reasons: list[str] | None = None,
) -> TestResult:
    """Safely construct a TestResult model instance across varying model schemas."""
    test_id = getattr(test, "id", None) or getattr(test, "test_id", "") or "UNKNOWN"
    test_name = getattr(test, "name", None) or getattr(test, "test_name", "") or test_id
    norm_status = status.lower()
    if norm_status not in ("pass", "fail", "error", "skip"):
        norm_status = "error"

    now = datetime.now(UTC)
    steps = redact_data(jev_steps) if jev_steps is not None else []
    redacted_error = redact_text(error) if error is not None else None
    redacted_console = redact_data(console_logs or [])
    redacted_network = redact_data(network_errors or [])
    redacted_cleanup = [redact_text(e) for e in (cleanup_errors or [])]
    redacted_inconclusive_reasons = [redact_text(r) for r in (inconclusive_reasons or [])]
    redacted_verifications = [
        vr.model_copy(
            update={
                "message": redact_text(vr.message),
                "actual_value": (
                    redact_text(vr.actual_value) if vr.actual_value is not None else None
                ),
            }
        )
        if hasattr(vr, "model_copy")
        else vr
        for vr in verifications
    ]
    if diagnosis is not None and hasattr(diagnosis, "model_copy"):
        diagnosis = diagnosis.model_copy(
            update={
                "summary": redact_text(diagnosis.summary),
                "evidence": [redact_text(e) for e in diagnosis.evidence],
                "remediation": redact_text(diagnosis.remediation),
            }
        )

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
            kwargs["verification_results"] = redacted_verifications
        elif "verifications" in fields:
            kwargs["verifications"] = redacted_verifications
        if "error_message" in fields:
            kwargs["error_message"] = redacted_error
        elif "error" in fields:
            kwargs["error"] = redacted_error
        if "screenshot_path" in fields:
            kwargs["screenshot_path"] = screenshot_path
        if "timestamp" in fields:
            kwargs["timestamp"] = now
        if "console_logs" in fields:
            kwargs["console_logs"] = redacted_console
        if "network_errors" in fields:
            kwargs["network_errors"] = redacted_network
        if "diagnosis" in fields:
            kwargs["diagnosis"] = diagnosis
        if "attempts" in fields:
            kwargs["attempts"] = attempts or []
        if "flaky" in fields:
            kwargs["flaky"] = flaky
        if "failure_category" in fields:
            kwargs["failure_category"] = failure_category
        if "created_entities" in fields:
            kwargs["created_entities"] = created_entities or []
        if "cleanup_errors" in fields:
            kwargs["cleanup_errors"] = redacted_cleanup
        if "inconclusive" in fields:
            kwargs["inconclusive"] = inconclusive
        if "inconclusive_reasons" in fields:
            kwargs["inconclusive_reasons"] = redacted_inconclusive_reasons
        return TestResult(**kwargs)

    return TestResult(
        test_id=test_id,
        status=norm_status,
        duration_seconds=duration,
        jev_steps=steps,
        verification_results=redacted_verifications,
        error_message=redacted_error,
        screenshot_path=screenshot_path,
        timestamp=now,
        console_logs=redacted_console,
        network_errors=redacted_network,
        diagnosis=diagnosis,
        attempts=attempts or [],
        flaky=flaky,
        failure_category=failure_category,
        created_entities=created_entities or [],
        cleanup_errors=redacted_cleanup,
        inconclusive=inconclusive,
        inconclusive_reasons=redacted_inconclusive_reasons,
    )


def _build_verification_result(
    passed: bool,
    expectation_type: str,
    message: str | None = None,
    actual: Any = None,
    expected: Any = None,
    details: dict[str, Any] | None = None,
    expectation: Any = None,
    inconclusive: bool = False,
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
            if expectation_type
            in (
                "dom",
                "url",
                "api",
                "visual",
                "semantic",
                "download",
                "a11y",
                "business_rule",
            )
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
        if "inconclusive" in fields:
            kwargs["inconclusive"] = inconclusive
        return VerificationResult(**kwargs)

    return VerificationResult(
        expectation=exp_obj,
        passed=passed,
        actual_value=str(actual) if actual is not None else None,
        message=msg,
        inconclusive=inconclusive,
    )


def _build_run_summary(
    total: int,
    passed: int,
    failed: int,
    skipped: int,
    errors: int,
    duration: float,
    inconclusive: int = 0,
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
        if "inconclusive" in fields:
            kwargs["inconclusive"] = inconclusive
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
        inconclusive=inconclusive,
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
        inconclusive=sum(1 for r in results if getattr(r, "inconclusive", False)),
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
    _group_tests_by_dependency = staticmethod(_group_tests_by_dependency)

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
        allowed_origins: list[str] | None = None,
        allow_cross_origin: bool = False,
        workers: int = 1,
        max_retries: int = 0,
        roles: dict[str, str | dict[str, Any]] | None = None,
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
        if workers < 1 or workers > 32:
            raise ValueError(f"workers must be between 1 and 32, got {workers}")
        if max_retries < 0 or max_retries > 5:
            raise ValueError(f"max_retries must be between 0 and 5, got {max_retries}")

        self.headless = headless
        self.cdp_url = cdp_url
        self.user_data_dir = user_data_dir
        self.storage_state = storage_state
        self.reuse_existing_context = reuse_existing_context
        self.reuse_existing_page = reuse_existing_page
        self.allowed_origins = list(allowed_origins or [])
        self.allow_cross_origin = allow_cross_origin
        self.workers = workers
        self.max_retries = max_retries
        self.roles: dict[str, str | dict[str, Any]] = dict(roles or {})
        self.screenshots_dir = (
            Path(screenshots_dir) if screenshots_dir is not None else Path("./screenshots")
        )

        try:
            self.jev_runner = JevRunner(screenshots_dir=screenshots_dir)
        except TypeError:
            self.jev_runner = JevRunner()

        self.dom_verifier = DomVerifier()
        self.url_verifier = UrlVerifier()
        self.a11y_verifier = AccessibilityVerifier()
        self.download_verifier = DownloadVerifier()

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
        self._current_allowed_origins: list[str] = list(self.allowed_origins)
        self._current_allow_cross_origin: bool = self.allow_cross_origin
        self._current_roles: dict[str, str | dict[str, Any]] = dict(self.roles)
        self._current_suite_setup_fixtures: list[Any] = []
        self._current_suite_teardown_fixtures: list[Any] = []

    async def run_suite(
        self,
        suite: TestSuite,
        workers: int | None = None,
        max_retries: int | None = None,
        cancel_event: asyncio.Event | None = None,
    ) -> TestRunReport:
        """Run all tests in a suite sequentially or across bounded parallel workers."""
        effective_workers = workers if workers is not None else self.workers
        effective_retries = max_retries if max_retries is not None else self.max_retries
        if effective_workers < 1 or effective_workers > 32:
            raise ValueError(f"workers must be between 1 and 32, got {effective_workers}")
        if effective_retries < 0 or effective_retries > 5:
            raise ValueError(f"max_retries must be between 0 and 5, got {effective_retries}")

        run_id = str(uuid4())
        self._failed_test_ids.clear()
        started_at = datetime.now(UTC)
        suite_start_time = time.perf_counter()

        suite_name = getattr(suite, "name", None) or "Test Suite"
        base_url = getattr(suite, "base_url", "") or ""
        if not base_url or not is_safe_url_scheme(base_url):
            raise ValueError(f"Invalid or unsafe suite base_url: '{base_url}'")
        self._current_base_url = base_url
        suite_allowed = list(getattr(suite, "allowed_origins", []) or [])
        self._current_allowed_origins = [base_url, *suite_allowed, *self.allowed_origins]
        self._current_allow_cross_origin = (
            bool(getattr(suite, "allow_cross_origin", False)) or self.allow_cross_origin
        )
        suite_roles = dict(getattr(suite, "roles", None) or {})
        self._current_roles = {**suite_roles, **self.roles}
        self._current_suite_setup_fixtures = list(getattr(suite, "setup_fixtures", None) or [])
        self._current_suite_teardown_fixtures = list(getattr(suite, "teardown_fixtures", None) or [])

        tests: list[TestCase] = getattr(suite, "tests", [])
        total_tests = len(tests)

        self.console.print("\n[bold cyan]AIQA Test Runner[/bold cyan]")
        self.console.print(f"[dim]{'─' * 32}[/dim]")
        if base_url:
            self.console.print(f"[bold]Target:[/bold] {base_url}")
        self.console.print(f"[bold]Suite:[/bold]  {suite_name}")
        self.console.print(f"[bold]Tests:[/bold]  {total_tests} loaded (workers={effective_workers})")
        self.console.print(f"[dim]Run ID: {run_id}[/dim]")
        self.console.print("\n[bold]Running...[/bold]\n")

        results_by_id: dict[str, TestResult] = {}
        groups = _group_tests_by_dependency(tests)

        if effective_workers <= 1:
            for group in groups:
                for test in group:
                    if cancel_event is not None and cancel_event.is_set():
                        results_by_id[test.id] = _build_test_result(
                            test=test,
                            status="skip",
                            duration=0.0,
                            verifications=[],
                            error="Skipped: test run cancelled",
                        )
                        continue
                    result = await self.run_test(test, max_retries=effective_retries)
                    results_by_id[test.id] = result
        else:
            semaphore = asyncio.Semaphore(effective_workers)

            async def _run_group(group: list[TestCase]) -> None:
                async with semaphore:
                    for test in group:
                        if cancel_event is not None and cancel_event.is_set():
                            results_by_id[test.id] = _build_test_result(
                                test=test,
                                status="skip",
                                duration=0.0,
                                verifications=[],
                                error="Skipped: test run cancelled",
                            )
                            continue
                        res = await self.run_test(test, max_retries=effective_retries)
                        results_by_id[test.id] = res

            tasks = [asyncio.create_task(_run_group(g)) for g in groups]
            try:
                await asyncio.gather(*tasks)
            except asyncio.CancelledError:
                for t in tasks:
                    if not t.done():
                        t.cancel()
                await asyncio.gather(*tasks, return_exceptions=True)
                for test in tests:
                    if test.id not in results_by_id:
                        results_by_id[test.id] = _build_test_result(
                            test=test,
                            status="skip",
                            duration=0.0,
                            verifications=[],
                            error="Skipped: test run cancelled",
                        )

        ordered_tests = [t for group in groups for t in group] if groups else list(tests)
        results: list[TestResult] = [
            results_by_id.get(
                t.id,
                _build_test_result(
                    test=t,
                    status="skip",
                    duration=0.0,
                    verifications=[],
                    error="Skipped: test run cancelled",
                ),
            )
            for t in ordered_tests
        ]

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

        for test, result in zip(ordered_tests, results):
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

    async def run_test(
        self,
        test: TestCase,
        max_retries: int | None = None,
    ) -> TestResult:
        """Execute a single test with optional retry on transient infrastructure failures."""
        effective_retries = max_retries if max_retries is not None else self.max_retries
        test_id = getattr(test, "id", None) or getattr(test, "test_id", "") or "UNKNOWN"
        attempts: list[AttemptRecord] = []

        result = await self._run_single_attempt(test)
        category = classify_failure(result)
        attempts.append(
            AttemptRecord(
                attempt=1,
                status=result.status,
                duration_seconds=result.duration_seconds,
                error_message=result.error_message,
                failure_category=category,
            )
        )

        attempt_num = 1
        while category == "transient_infra" and attempt_num <= effective_retries:
            attempt_num += 1
            self._failed_test_ids.discard(test_id)
            result = await self._run_single_attempt(test)
            category = classify_failure(result)
            attempts.append(
                AttemptRecord(
                    attempt=attempt_num,
                    status=result.status,
                    duration_seconds=result.duration_seconds,
                    error_message=result.error_message,
                    failure_category=category,
                )
            )

        is_flaky = len(attempts) > 1 and result.status == "pass"
        total_duration = round(sum(a.duration_seconds for a in attempts), 2)
        if hasattr(result, "model_copy"):
            return result.model_copy(
                update={
                    "attempts": attempts,
                    "flaky": is_flaky,
                    "failure_category": category,
                    "duration_seconds": total_duration or result.duration_seconds,
                }
            )
        return result

    async def _run_single_attempt(self, test: TestCase) -> TestResult:
        """
        Execute a single test attempt:
        1. Open browser session
        2. Run Jev with the test goal (enforcing per-test timeout)
        3. For each expected verification:
           - Route to appropriate verifier (dom/url/semantic/a11y/download/business_rule)
        4. Determine overall PASS/FAIL (and inconclusive business rules)
        5. Take screenshot on failure
        6. Return TestResult
        """
        test_id = getattr(test, "id", None) or getattr(test, "test_id", "") or "UNKNOWN"
        test_timeout = float(getattr(test, "timeout", None) or 60)

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
                and not start_url.startswith(("http://", "https://", "file://", "about:", "javascript:", "vbscript:"))
                and hasattr(test, "model_copy")
            ):
                test = test.model_copy(update={"start_url": urljoin(base_url, start_url)})

        policy_error = self._policy_validation_error(test)
        if policy_error:
            self._failed_test_ids.add(test_id)
            self._print_test_progress(test, "fail", 0.0)
            return _build_test_result(
                test=test,
                status="fail",
                duration=0.0,
                verifications=[],
                error=policy_error,
            )

        try:
            test = resolve_test_case_secrets(test)
            role_name = getattr(test, "role", None)
            if role_name:
                effective_storage = resolve_role_storage_state(
                    role_name,
                    self._current_roles,
                    fallback_storage_state=self.storage_state,
                )
            else:
                effective_storage = self.storage_state
                if isinstance(effective_storage, dict) or (
                    isinstance(effective_storage, (str, Path))
                    and Path(effective_storage).exists()
                ):
                    validate_storage_state(effective_storage)
        except Exception as auth_exc:  # noqa: BLE001
            self._failed_test_ids.add(test_id)
            self._print_test_progress(test, "fail", 0.0)
            return _build_test_result(
                test=test,
                status="fail",
                duration=0.0,
                verifications=[],
                error=f"Authentication / secret validation failed: {auth_exc}",
            )

        start_time = time.perf_counter()
        verifications: list[VerificationResult] = []
        screenshot_path: str | None = None
        jev_steps: list[dict[str, Any]] = []
        interaction_unchanged = False

        fixture_mgr = FixtureLifecycleManager(
            base_url=base_url or getattr(test, "start_url", None)
        )
        setup_specs = [
            *self._current_suite_setup_fixtures,
            *(getattr(test, "setup_fixtures", None) or []),
        ]
        teardown_specs = [
            *self._current_suite_teardown_fixtures,
            *(getattr(test, "teardown_fixtures", None) or []),
        ]
        result_out: TestResult | None = None

        try:
            if setup_specs:
                remaining_setup = test_timeout - (time.perf_counter() - start_time)
                if remaining_setup <= 0:
                    raise TimeoutError(f"Test execution timed out after {test_timeout:.1f}s")
                await asyncio.wait_for(
                    fixture_mgr.run_setup(setup_specs),
                    timeout=remaining_setup,
                )
                test = fixture_mgr.bind_test_tokens(test)

            # 1. Open browser session (each test gets its own session for clean state)
            create_sig = inspect.signature(self._create_browser_session)
            session_cm = (
                self._create_browser_session(test=test, storage_state=effective_storage)
                if "test" in create_sig.parameters
                else self._create_browser_session()
            )
            async with session_cm as session:
                # 2. Run Jev with the test goal (enforcing per-test timeout)
                jev_failed = False
                jev_error: str | None = None
                try:
                    remaining_for_jev = test_timeout - (time.perf_counter() - start_time)
                    if remaining_for_jev <= 0:
                        raise TimeoutError
                    jev_result = await asyncio.wait_for(
                        self._execute_jev(session, test),
                        timeout=remaining_for_jev,
                    )
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

                        final_url = getattr(jev_result, "final_url", None) or ""
                        allow_cross = self._current_allow_cross_origin or self.allow_cross_origin
                        check_origins = (
                            list(self._current_allowed_origins)
                            or list(self.allowed_origins)
                            or ([self._current_base_url] if self._current_base_url else [])
                            or ([test.start_url] if extract_origin(test.start_url) else [])
                        )
                        if (
                            not jev_failed
                            and check_origins
                            and not allow_cross
                            and isinstance(final_url, str)
                            and final_url
                            and not is_origin_allowed(final_url, check_origins, allow_cross_origin=False)
                        ):
                            jev_failed = True
                            jev_error = (
                                f"Cross-origin redirect/navigation blocked: final URL '{final_url}' "
                                f"is outside allowed origins {check_origins}."
                            )
                except TimeoutError:
                    jev_failed = True
                    jev_error = f"Test execution timed out after {test_timeout:.1f}s"
                    logger.warning("Test %s timed out after %.1fs", test_id, test_timeout)
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
                    except Exception as state_err:  # noqa: BLE001
                        logger.debug("Could not capture page state on error: %s", state_err)

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

                    result_out = _build_test_result(
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
                else:
                    # 3. For each expected verification:
                    #    - Route to appropriate verifier (dom/url/semantic/a11y/download/business_rule)
                    expectations = (
                        getattr(test, "expected", None)
                        or getattr(test, "expectations", None)
                        or getattr(test, "verifications", None)
                        or []
                    )

                    for expectation in expectations:
                        exp_type = self._get_expectation_type(expectation)
                        try:
                            remaining_verify = test_timeout - (time.perf_counter() - start_time)
                            if remaining_verify <= 0:
                                raise TimeoutError
                            norm_exp_type = exp_type.strip().lower()
                            if norm_exp_type == "business_rule" or (
                                getattr(expectation, "inconclusive_if_missing_oracle", False)
                                and getattr(expectation, "oracle", None) is None
                                and getattr(expectation, "value", None) is None
                            ):
                                v_res = await asyncio.wait_for(
                                    self._verify_business_rule(session, expectation),
                                    timeout=remaining_verify,
                                )
                            else:
                                verifier = self._route_verifier(exp_type)
                                v_res = await asyncio.wait_for(
                                    self._run_verifier(verifier, session, expectation, exp_type),
                                    timeout=remaining_verify,
                                )
                            verifications.append(v_res)
                        except TimeoutError:
                            v_res = _build_verification_result(
                                passed=False,
                                expectation_type=exp_type,
                                message=f"Verification timed out after {test_timeout:.1f}s",
                                expectation=expectation,
                            )
                            verifications.append(v_res)
                            break
                        except Exception as v_err:
                            logger.exception("Error executing verification for test %s", test_id)
                            v_res = _build_verification_result(
                                passed=False,
                                expectation_type=exp_type,
                                message=f"Verification execution error: {v_err}",
                                expectation=expectation,
                            )
                            verifications.append(v_res)

                    inconclusive_vrs = [
                        vr for vr in verifications if getattr(vr, "inconclusive", False)
                    ]
                    inconclusive_reasons = [
                        getattr(vr, "message", "")
                        for vr in inconclusive_vrs
                        if getattr(vr, "message", "")
                    ]
                    is_inconclusive = len(inconclusive_vrs) > 0

                    # 4. Determine overall PASS/FAIL
                    # FAIL means Jev succeeded but verification didn't pass (or was inconclusive)
                    all_passed = (
                        all(
                            getattr(vr, "passed", getattr(vr, "success", False))
                            and not getattr(vr, "inconclusive", False)
                            for vr in verifications
                        )
                        and not interaction_unchanged
                    )
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
                            or getattr(vr, "inconclusive", False)
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
                    except Exception as state_err:  # noqa: BLE001
                        logger.debug("Could not capture page state after verification: %s", state_err)

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
                            inconclusive=is_inconclusive,
                            inconclusive_reasons=inconclusive_reasons,
                        )
                        try:
                            diag = self.failure_analyzer.diagnose(test, temp_res, p_state)
                        except Exception as d_err:  # noqa: BLE001
                            logger.debug("Failure analyzer error: %s", d_err)

                    result_out = _build_test_result(
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
                        inconclusive=is_inconclusive,
                        inconclusive_reasons=inconclusive_reasons,
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
            result_out = _build_test_result(
                test=test,
                status="error",
                duration=duration,
                verifications=verifications,
                error=f"Test runtime error: {unhandled_err}",
                screenshot_path=screenshot_path,
                jev_steps=jev_steps,
                diagnosis=diag,
            )
        finally:
            cleanup_errors: list[str] = []
            if teardown_specs:
                try:
                    cleanup_errors = await fixture_mgr.run_teardown(teardown_specs)
                except Exception as td_exc:  # noqa: BLE001
                    cleanup_errors = [f"Teardown error: {td_exc}"]
            if result_out is not None and (fixture_mgr.created_entities or cleanup_errors):
                if hasattr(result_out, "model_copy"):
                    result_out = result_out.model_copy(
                        update={
                            "created_entities": list(fixture_mgr.created_entities),
                            "cleanup_errors": [redact_text(e) for e in cleanup_errors],
                        }
                    )
                else:
                    result_out.created_entities = list(fixture_mgr.created_entities)
                    result_out.cleanup_errors = [redact_text(e) for e in cleanup_errors]

        assert result_out is not None
        return result_out

    async def _verify_business_rule(
        self,
        session: BrowserSession,
        expectation: Any,
    ) -> VerificationResult:
        """Verify a business rule expectation or mark it inconclusive if no oracle is supplied."""
        desc = getattr(expectation, "description", None) or "Business rule verification"
        oracle = getattr(expectation, "oracle", None)
        value = getattr(expectation, "value", None)
        selector = getattr(expectation, "selector", None)
        criterion = oracle if oracle is not None else value

        if criterion is None or str(criterion).strip() == "":
            return _build_verification_result(
                passed=False,
                expectation_type="business_rule",
                message=(
                    f"Inconclusive: business rule '{desc}' cannot be verified "
                    "without an explicit oracle or expected value criterion."
                ),
                expectation=expectation,
                inconclusive=True,
            )

        if selector:
            dom_exp = Expectation(
                type="dom",
                description=desc,
                selector=selector,
                condition=getattr(expectation, "condition", "contains"),
                value=str(criterion),
            )
            res = await self._run_verifier(self.dom_verifier, session, dom_exp, "dom")
            return res.model_copy(update={"expectation": expectation})

        page = getattr(session, "page", None)
        actual_text = ""
        if page is not None:
            try:
                if hasattr(page, "inner_text"):
                    actual_text = await page.inner_text("body")
            except Exception:  # noqa: BLE001
                actual_text = ""
        elif hasattr(session, "get_page_state"):
            try:
                st = await session.get_page_state()
                if isinstance(st, dict):
                    actual_text = str(st.get("text_snippet") or st.get("title") or "")
            except Exception:  # noqa: BLE001
                actual_text = ""

        crit_str = str(criterion)
        passed = crit_str.lower() in actual_text.lower()
        return _build_verification_result(
            passed=passed,
            expectation_type="business_rule",
            message=(
                f"Business rule verified: found '{crit_str}'"
                if passed
                else f"Business rule failed: expected '{crit_str}' not found in page content"
            ),
            actual=actual_text[:200] if actual_text else None,
            expected=crit_str,
            expectation=expectation,
            inconclusive=False,
        )

    def _route_verifier(self, expectation_type: str):
        """Route to the correct verifier based on expectation type."""
        norm_type = expectation_type.strip().lower()
        if norm_type in ("dom", "selector", "element", "text") or norm_type.startswith("dom"):
            return self.dom_verifier
        elif norm_type in ("url", "path", "route", "redirect") or norm_type.startswith("url"):
            return self.url_verifier
        elif norm_type in ("a11y", "accessibility", "wcag", "aria"):
            return self.a11y_verifier
        elif norm_type in ("download", "file_download"):
            return self.download_verifier
        elif norm_type in ("semantic", "llm", "ai", "visual", "meaning", "business_rule") or norm_type.startswith(
            "semantic"
        ):
            return self.semantic_verifier
        else:
            raise ValueError(f"Unknown verification expectation type: '{expectation_type}'")

    def _policy_validation_error(self, test: TestCase) -> str | None:
        """Validate URL schemes and origin constraints before opening a browser."""
        start_url = getattr(test, "start_url", "") or ""
        if not start_url or not is_safe_url_scheme(start_url):
            return f"Test start_url '{start_url}' uses an unsafe or invalid URL scheme."

        effective_origins = list(self._current_allowed_origins)
        if not effective_origins and self.allowed_origins:
            effective_origins = list(self.allowed_origins)
        if not effective_origins and self._current_base_url:
            effective_origins = [self._current_base_url]
        allow_cross = self._current_allow_cross_origin or self.allow_cross_origin

        if (
            effective_origins
            and not allow_cross
            and not is_origin_allowed(start_url, effective_origins, allow_cross_origin=False)
        ):
            return (
                f"Test start_url '{start_url}' violates origin constraint policy. "
                f"Allowed origins: {effective_origins}. Set allow_cross_origin=True to permit."
            )

        check_origins = effective_origins or ([start_url] if extract_origin(start_url) else [])
        actions = getattr(test, "actions", None) or []
        for action in actions:
            if getattr(action, "action", None) == "navigate":
                nav_url = getattr(action, "url", "")
                if not is_safe_url_scheme(nav_url):
                    return f"Navigate action URL '{nav_url}' uses an unsafe URL scheme."
                if (
                    check_origins
                    and not allow_cross
                    and not is_origin_allowed(nav_url, check_origins, allow_cross_origin=False)
                ):
                    return (
                        f"Navigate action to '{nav_url}' violates origin constraint policy. "
                        f"Allowed origins: {check_origins}. Set allow_cross_origin=True to permit."
                    )
        return None

    def _postcondition_validation_error(self, test: TestCase) -> str | None:
        """Reject tests that cannot deterministically prove their requested outcome."""
        expectations = list(getattr(test, "expected", None) or [])
        if not expectations:
            return "Test requires at least one deterministic postcondition."

        deterministic = [
            expectation
            for expectation in expectations
            if self._get_expectation_type(expectation).strip().lower()
            in {"dom", "url", "download", "a11y", "accessibility", "business_rule"}
        ]
        if not deterministic:
            return "Interaction test requires at least one deterministic DOM or URL postcondition."

        meaningful = [
            expectation
            for expectation in deterministic
            if getattr(expectation, "selector", None)
            or getattr(expectation, "value", None)
            or getattr(expectation, "oracle", None)
            or getattr(expectation, "inconclusive_if_missing_oracle", False)
            or self._get_expectation_type(expectation).strip().lower()
            in {"a11y", "accessibility", "business_rule"}
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
                and getattr(action, "action", None) in {"click", "fill", "press", "upload", "popup"}
                for action in actions
            )
        return any(
            step.get("required", True)
            and step.get("status") == "completed"
            and step.get("action") in {"click", "fill", "press", "upload", "popup"}
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
                            if _string_references_test_id(pre, failed_id):
                                return failed_id
                    elif isinstance(pre, dict):
                        dep_id = pre.get("test_id") or pre.get("depends_on") or pre.get("id")
                        if dep_id and str(dep_id) in self._failed_test_ids:
                            return str(dep_id)
                    elif hasattr(pre, "test_id") and str(pre.test_id) in self._failed_test_ids:
                        return str(pre.test_id)
            elif isinstance(preconditions, str):
                for failed_id in self._failed_test_ids:
                    if _string_references_test_id(preconditions, failed_id):
                        return failed_id

        return None

    @asynccontextmanager
    async def _create_browser_session(
        self,
        test: TestCase | None = None,
        storage_state: Any = None,
    ):
        """Context manager to ensure clean browser session lifecycle for each test."""
        session: BrowserSession
        effective_storage = storage_state if storage_state is not None else self.storage_state
        extra_kwargs: dict[str, Any] = {}
        if test is not None:
            if getattr(test, "viewport", None) is not None:
                extra_kwargs["viewport"] = test.viewport
            if getattr(test, "is_mobile", False):
                extra_kwargs["is_mobile"] = True
            if getattr(test, "user_agent", None):
                extra_kwargs["user_agent"] = test.user_agent

        try:
            session = BrowserSession(
                headless=self.headless,
                cdp_url=self.cdp_url,
                user_data_dir=self.user_data_dir,
                storage_state=effective_storage,
                reuse_existing_context=self.reuse_existing_context,
                reuse_existing_page=self.reuse_existing_page,
                **extra_kwargs,
            )
        except TypeError:
            try:
                session = BrowserSession(
                    headless=self.headless,
                    cdp_url=self.cdp_url,
                    user_data_dir=self.user_data_dir,
                    storage_state=effective_storage,
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
