"""Regression tests for the public benchmark methodology metadata."""

from scripts.live_benchmark_experiment import build_experiment_record
from scripts.run_1000_benchmark import compute_statistics


def test_scale_summary_identifies_direct_playwright_scope_without_model_comparison():
    results = [
        {"status": "pass", "duration_ms": 100.0},
        {"status": "fail", "duration_ms": 200.0},
    ]

    summary = compute_statistics(
        results,
        wall_clock_seconds=0.5,
        concurrency=2,
        browser_version="123.0.1",
    )

    assert summary["benchmark"]["kind"] == "direct_playwright_dom"
    assert summary["benchmark"]["aiqa_pipeline_exercised"] is False
    assert summary["benchmark"]["concurrency"] == 2
    assert summary["benchmark"]["comparison_scope"] == "single_harness_only"
    assert summary["environment"]["browser_name"] == "Chromium"
    assert summary["environment"]["browser_version"] == "123.0.1"
    assert "comparative_projections" not in summary
    assert "speedup" not in repr(summary).lower()


def test_live_record_does_not_describe_direct_playwright_as_aiqa_pipeline():
    direct_result = {"paradigm": "Direct Playwright DOM locator", "passed": True}
    coordinate_result = {
        "paradigm": "Local Playwright coordinate simulation",
        "passed": True,
    }

    record = build_experiment_record(
        target_url="https://example.test/",
        link_text="Books",
        expected_url_substr="/books",
        direct_result=direct_result,
        coordinate_result=coordinate_result,
        timestamp="2026-09-25T13:00:00+00:00",
    )

    assert record["experiment"]["aiqa_pipeline_exercised"] is False
    assert record["experiment"]["model_inference_exercised"] is False
    assert record["experiment"]["comparison_scope"] == "local_browser_components_only"
    assert record["results"]["direct_playwright_dom"] == direct_result
    assert record["results"]["local_coordinate_simulation"] == coordinate_result
