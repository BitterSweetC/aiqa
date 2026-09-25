"""Phase 2 evaluation harness, deterministic sharding, parallel isolation, and transient retry tests."""

from __future__ import annotations

import asyncio
import json
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

import pytest
from click.testing import CliRunner
from rich.console import Console

from aiqa.benchmarks.harness import (
    FIXTURE_SITE_VERSION,
    BenchmarkHarness,
    FixtureSiteServer,
    load_frozen_suite,
)
from aiqa.cli import cli
from aiqa.models.test_case import (
    ClickAction,
    Expectation,
    NavigateAction,
    TestCase,
    TestRunReport,
    TestSuite,
)
from aiqa.orchestrator.runner import (
    TestRunner,
    classify_failure,
    parse_shard_spec,
    select_tests,
)
from aiqa.reports.json_report import JsonReporter
from aiqa.reports.junit_report import JUnitReporter


@pytest.mark.asyncio
async def test_phase2_reproducible_benchmark_harness_and_baseline_comparison(
    tmp_path: Path,
) -> None:
    """BenchmarkHarness runs the full AIQA pipeline on frozen suite v1.0 and compares baseline."""
    harness = BenchmarkHarness(output_dir=tmp_path / "bench", headless=True)
    report = await harness.run(
        workers=2,
        warmup_runs=1,
        scale_multiplier=1,
        compare_baseline=True,
    )

    assert report.fixture_site_version == FIXTURE_SITE_VERSION
    assert report.pipeline_mode == "aiqa_full_pipeline"
    assert "TestRunner" in report.pipeline_coverage
    assert "JevRunner" in report.pipeline_coverage
    assert "ActionDriver" in report.pipeline_coverage
    assert report.total_tests == 6
    assert report.passed_tests == 6
    assert report.failed_tests == 0
    assert report.error_tests == 0
    assert report.success_rate == 1.0
    assert report.failure_rate == 0.0
    assert report.warmup_runs == 1
    assert report.warmup_duration_seconds > 0.0
    assert report.wall_time_seconds > 0.0
    assert report.latency_ms["p50"] > 0.0
    assert report.latency_ms["p95"] >= report.latency_ms["p50"]
    assert report.artifacts["json_report_bytes"] > 0
    assert report.artifacts["junit_report_bytes"] > 0
    assert report.artifacts["html_report_bytes"] > 0
    assert report.model_telemetry["model_execution_used"] is False
    assert report.model_telemetry["measured_cost_usd"] == 0.0

    assert report.baseline_comparison is not None
    assert report.baseline_comparison["same_workload"] is True
    assert report.baseline_comparison["same_concurrency"] is True
    assert report.baseline_comparison["same_failure_policy"] is True
    assert report.baseline_comparison["pass_rate_agreement"] is True
    assert (tmp_path / "bench" / "benchmark_summary.json").exists()


@pytest.mark.asyncio
async def test_phase2_sharding_selection_and_report_aggregation(tmp_path: Path) -> None:
    """Shards partition selected tests disjointly, keep dependencies together, and aggregate."""
    assert parse_shard_spec("2/4") == (2, 4)
    with pytest.raises(ValueError, match="Invalid shard"):
        parse_shard_spec("5/4")
    with pytest.raises(ValueError, match="Invalid shard"):
        parse_shard_spec("0/2")

    with FixtureSiteServer() as server:
        full_suite = load_frozen_suite(server.base_url)
        # Make BENCH-004 depend on BENCH-002 to verify dependency chain co-location
        tests = list(full_suite.tests)
        tests[3] = tests[3].model_copy(update={"preconditions": ["Requires BENCH-002"]})
        suite_with_dep = full_suite.model_copy(update={"tests": tests})

        # Verify tag selection
        search_only = select_tests(suite_with_dep, tags=["search"])
        assert [t.id for t in search_only.tests] == ["BENCH-003"]

        # Verify 3-way sharding covers every test exactly once and keeps BENCH-002 + BENCH-004 together
        shards = [select_tests(suite_with_dep, shard=f"{i}/3") for i in (1, 2, 3)]
        all_shard_ids: list[str] = []
        for s in shards:
            ids = [t.id for t in s.tests]
            if "BENCH-002" in ids or "BENCH-004" in ids:
                assert "BENCH-002" in ids and "BENCH-004" in ids
            all_shard_ids.extend(ids)

        assert len(all_shard_ids) == len(suite_with_dep.tests)
        assert set(all_shard_ids) == {t.id for t in suite_with_dep.tests}

        # Execute each shard and aggregate reports
        shard_reports: list[TestRunReport] = []
        shard_paths: list[Path] = []
        for idx, shard_suite in enumerate(shards, start=1):
            runner = TestRunner(
                headless=True,
                screenshots_dir=tmp_path / f"shots_{idx}",
                console=Console(quiet=True),
                workers=2,
            )
            rep = await runner.run_suite(shard_suite)
            shard_reports.append(rep)
            p = JsonReporter(output_dir=tmp_path).save(rep, filename=f"shard_{idx}.json")
            shard_paths.append(p)

        aggregated = TestRunReport.aggregate(shard_reports, run_id="agg-run-123")
        assert aggregated.summary.total == 6
        assert aggregated.summary.passed == 6
        assert aggregated.summary.pass_rate == 1.0

        # Duplicate shard aggregation must fail closed
        with pytest.raises(ValueError, match="Duplicate test_id"):
            TestRunReport.aggregate([shard_reports[0], shard_reports[0]])

        # Verify CLI `aiqa report` multi-input aggregation
        agg_json = tmp_path / "aggregated_cli.json"
        agg_html = tmp_path / "aggregated_cli.html"
        agg_junit = tmp_path / "aggregated_cli.xml"
        cli_res = CliRunner().invoke(
            cli,
            [
                "report",
                "--input",
                str(shard_paths[0]),
                "--input",
                str(shard_paths[1]),
                "--input",
                str(shard_paths[2]),
                "--output-json",
                str(agg_json),
                "--html",
                str(agg_html),
                "--junit",
                str(agg_junit),
            ],
        )
        assert cli_res.exit_code == 0
        assert agg_json.exists()
        assert agg_html.exists()
        assert agg_junit.exists()


@pytest.mark.asyncio
async def test_phase2_parallel_workers_isolation_and_cancellation(tmp_path: Path) -> None:
    """Parallel workers maintain isolated browser contexts and support clean cancellation."""
    with FixtureSiteServer() as server:
        # Run 3 cart tests in parallel; if contexts were shared, #cart-count or cookies would collide
        parallel_cases = [
            TestCase(
                id=f"ISO-{i:03d}",
                name=f"Isolated Cart Add {i}",
                start_url=f"{server.base_url}/catalog",
                actions=[ClickAction(action="click", selector="#add-to-cart-1")],
                expected=[
                    Expectation(
                        type="dom",
                        description="Cart count is exactly 1 in isolated session",
                        selector="#cart-count",
                        value="1",
                    )
                ],
            )
            for i in range(1, 5)
        ]
        iso_suite = TestSuite(
            name="Parallel Isolation Suite",
            base_url=server.base_url,
            tests=parallel_cases,
        )
        runner = TestRunner(
            headless=True,
            screenshots_dir=tmp_path / "iso_shots",
            console=Console(quiet=True),
            workers=3,
        )
        report = await runner.run_suite(iso_suite, workers=3)
        assert report.summary.total == 4
        assert report.summary.passed == 4

        # Verify pre-set cancellation event skips unstarted tests cleanly
        cancel_event = asyncio.Event()
        cancel_event.set()
        cancelled_report = await runner.run_suite(
            iso_suite,
            workers=2,
            cancel_event=cancel_event,
        )
        assert cancelled_report.summary.skipped == 4
        assert all(r.status == "skip" for r in cancelled_report.results)


@pytest.mark.asyncio
async def test_phase2_transient_retry_vs_deterministic_assertion_failure(
    tmp_path: Path,
) -> None:
    """Retries apply only to transient_infra failures and never mask deterministic assertion failures."""
    with FixtureSiteServer() as server:
        flaky_case = TestCase(
            id="FLAKY-001",
            name="Transient connection reset on first attempt",
            start_url=f"{server.base_url}/",
            actions=[NavigateAction(action="navigate", url=f"{server.base_url}/")],
            expected=[
                Expectation(
                    type="dom",
                    description="Hero title visible",
                    selector="#hero-title",
                    value="AIQA Enterprise Fixture Store",
                )
            ],
        )
        det_fail_case = TestCase(
            id="DET-FAIL-001",
            name="Reproducible assertion failure must not retry",
            start_url=f"{server.base_url}/",
            actions=[NavigateAction(action="navigate", url=f"{server.base_url}/")],
            expected=[
                Expectation(
                    type="dom",
                    description="Deliberately wrong text",
                    selector="#hero-title",
                    value="NonExistentStoreTitle999",
                )
            ],
        )

        runner = TestRunner(
            headless=True,
            screenshots_dir=tmp_path / "retry_shots",
            console=Console(quiet=True),
            max_retries=2,
        )
        real_exec = runner._execute_jev
        call_counts: dict[str, int] = {"FLAKY-001": 0, "DET-FAIL-001": 0}

        async def _flaky_first_jev(session: Any, test: TestCase) -> Any:
            call_counts[test.id] = call_counts.get(test.id, 0) + 1
            if test.id == "FLAKY-001" and call_counts[test.id] == 1:
                raise RuntimeError("Page.goto: net::ERR_CONNECTION_RESET at http://127.0.0.1/")
            return await real_exec(session, test)

        runner._execute_jev = _flaky_first_jev  # type: ignore[method-assign]

        suite = TestSuite(
            name="Retry Classification Suite",
            base_url=server.base_url,
            tests=[flaky_case, det_fail_case],
        )
        report = await runner.run_suite(suite, max_retries=2)

        flaky_res = report.results[0]
        det_res = report.results[1]

        # 1. Transient infra failure retried once and marked flaky
        assert flaky_res.status == "pass"
        assert flaky_res.flaky is True
        assert len(flaky_res.attempts) == 2
        assert flaky_res.attempts[0].status == "error"
        assert flaky_res.attempts[0].failure_category == "transient_infra"
        assert flaky_res.attempts[1].status == "pass"
        assert report.summary.flaky == 1

        # 2. Deterministic assertion failure is NEVER retried
        assert det_res.status == "fail"
        assert det_res.flaky is False
        assert det_res.failure_category == "deterministic_assertion"
        assert classify_failure(det_res) == "deterministic_assertion"
        assert len(det_res.attempts) == 1
        assert call_counts["DET-FAIL-001"] == 1

        # 3. Verify JSON and JUnit reports preserve attempts and flaky status
        json_path = JsonReporter(output_dir=tmp_path).save(report)
        junit_path = JUnitReporter(output_dir=tmp_path).save(report)

        json_payload = json.loads(json_path.read_text(encoding="utf-8"))
        assert json_payload["summary"]["flaky"] == 1
        assert len(json_payload["results"][0]["attempts"]) == 2
        assert json_payload["results"][0]["flaky"] is True

        xml_root = ET.fromstring(junit_path.read_text(encoding="utf-8"))
        sys_out_text = xml_root.find(".//testcase[@name='FLAKY-001']/system-out")
        assert sys_out_text is not None and sys_out_text.text is not None
        sys_out_data = json.loads(sys_out_text.text)
        assert sys_out_data["flaky"] is True
        assert len(sys_out_data["attempts"]) == 2
