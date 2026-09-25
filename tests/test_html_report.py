"""Unit tests for Stage 5 Interactive HTML Dashboard Generator."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from aiqa.models.test_case import (
    Expectation,
    FailureDiagnosis,
    RunSummary,
    TestResult,
    TestRunReport,
    VerificationResult,
)
from aiqa.reports.html_report import HtmlReporter


def _sample_report(tmp_path: Path) -> TestRunReport:
    exp = Expectation(
        type="dom",
        description="Cart badge displays 1",
        selector=".cart-count",
        value="1",
    )
    vr = VerificationResult(
        expectation=exp,
        passed=False,
        actual_value="0",
        message="Expected value '1' but got '0'",
    )
    diag = FailureDiagnosis(
        summary="Cart count failed to increment due to backend 500 error.",
        likely_cause="Backend API Failure (HTTP 500 on POST /api/cart)",
        evidence=["POST /api/cart failed with HTTP 500"],
        remediation="Check server logs for database lock or null pointer.",
        severity="critical",
    )
    # Create a dummy screenshot
    screen_file = tmp_path / "fail_screen.png"
    screen_file.write_bytes(b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00\x1f\x15c4\x00\x00\x00\nIDATx\x9cc\x00\x01\x00\x00\x05\x00\x01\r\n-\xb4\x00\x00\x00\x00IEND\xaeB`\x82")

    result = TestResult(
        test_id="CART-001",
        status="fail",
        duration_seconds=1.85,
        jev_steps=[
            {"step": 1, "action": "click", "details": "Clicked Add to Cart", "status": "completed"}
        ],
        verification_results=[vr],
        error_message="Verification failed",
        screenshot_path=str(screen_file),
        timestamp=datetime.now(UTC),
        network_errors=[{"url": "https://example.com/api/cart", "status": 500, "method": "POST"}],
        console_logs=[{"type": "error", "text": "Failed to load resource: the server responded with a status of 500"}],
        diagnosis=diag,
    )

    summary = RunSummary.from_results([result], duration_seconds=1.85)
    return TestRunReport(
        run_id="run-test-uuid-999",
        suite_name="E-Commerce Regression Suite",
        base_url="https://example.com",
        started_at=datetime.now(UTC),
        finished_at=datetime.now(UTC),
        results=[result],
        summary=summary,
    )


def test_html_reporter_generate(tmp_path: Path):
    """HtmlReporter generates valid standalone HTML with metrics, cards, and diagnosis."""
    report = _sample_report(tmp_path)
    reporter = HtmlReporter(output_dir=tmp_path)
    html_content = reporter.generate(report)

    assert "<!DOCTYPE html>" in html_content
    assert "E-Commerce Regression Suite" in html_content
    assert "https://example.com" in html_content
    assert "CART-001" in html_content
    assert "Failure Root-Cause Diagnosis" in html_content
    assert "Backend API Failure" in html_content
    assert "Check server logs" in html_content
    assert "data:image/png;base64," in html_content  # embedded screenshot
    assert "run-test-uuid-999" in html_content


def test_html_reporter_save(tmp_path: Path):
    """HtmlReporter saves HTML dashboard to target path."""
    report = _sample_report(tmp_path)
    reporter = HtmlReporter(output_dir=tmp_path)

    out_file = tmp_path / "custom_dashboard.html"
    saved = reporter.save(report, filename=out_file)

    assert saved.exists()
    assert saved == out_file
    text = saved.read_text(encoding="utf-8")
    assert "AIQA Test Execution Dashboard" in text
    assert "CART-001" in text


def test_html_reporter_neutralizes_script_breakout_and_unsafe_target_url(tmp_path: Path):
    report = _sample_report(tmp_path)
    report.suite_name = "</script><script>alert('suite')</script>"
    report.base_url = "javascript:alert('target')"
    report.results[0].jev_steps[0]["details"] = "</script><script>alert('trace')</script>"

    html_content = HtmlReporter(output_dir=tmp_path).generate(report)

    assert "const tests = [{" in html_content
    assert "</script><script>alert('trace')</script>" not in html_content
    assert "\\u003c/script\\u003e\\u003cscript\\u003ealert('trace')\\u003c/script\\u003e" in html_content
    assert 'href="javascript:' not in html_content
