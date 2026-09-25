"""JSON report generator for AIQA test runs."""

from __future__ import annotations

import importlib.metadata
import json
import platform
import sys
from datetime import UTC, datetime
from pathlib import Path

from aiqa.models.test_case import TestRunReport

REPORT_SCHEMA_VERSION = "1.0"


class JsonReporter:
    """Generates and manages JSON test reports."""

    def __init__(self, output_dir: Path | str = Path("./reports")) -> None:
        """Initialize the reporter with an output directory.

        Args:
            output_dir: Directory where JSON reports will be saved.
        """
        self.output_dir = Path(output_dir)

    def save(self, report: TestRunReport) -> Path:
        """Save a test run report as JSON.

        Filename format: run_YYYYMMDD_HHMMSS.json
        Returns the path to the saved report.

        Args:
            report: The TestRunReport instance to serialize.

        Returns:
            Path: The path to the saved JSON report file.
        """
        self.output_dir.mkdir(parents=True, exist_ok=True)

        if report.started_at is not None:
            timestamp = report.started_at.strftime("%Y%m%d_%H%M%S")
        else:
            timestamp = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")

        filename = f"run_{timestamp}.json"
        report_path = self.output_dir / filename

        payload = report.model_dump(mode="json")
        payload["schema_version"] = REPORT_SCHEMA_VERSION
        payload["metadata"] = {
            "generated_at": datetime.now(UTC).isoformat(),
            "runtime": {
                "python": platform.python_version(),
                "implementation": platform.python_implementation(),
                "platform": sys.platform,
                "aiqa": _package_version("aiqa"),
                "playwright": _package_version("playwright"),
            },
        }
        json_content = json.dumps(payload, indent=2, ensure_ascii=False)
        report_path.write_text(json_content, encoding="utf-8")
        return report_path

    def format_summary(self, report: TestRunReport) -> str:
        """Format a human-readable summary string.

        Args:
            report: The TestRunReport instance to summarize.

        Returns:
            str: Multi-line human-readable summary of the test run.
        """
        summary = report.summary
        duration_str = f"{summary.duration_seconds:.2f}s"
        pass_rate_pct = f"{summary.pass_rate * 100:.1f}%"

        started_str = (
            report.started_at.strftime("%Y-%m-%d %H:%M:%S") if report.started_at else "N/A"
        )
        finished_str = (
            report.finished_at.strftime("%Y-%m-%d %H:%M:%S") if report.finished_at else "N/A"
        )

        lines: list[str] = [
            "============================================================",
            "                   AIQA Test Run Summary",
            "============================================================",
            f"Suite:       {report.suite_name}",
            f"Target:      {report.base_url}",
            f"Run ID:      {report.run_id}",
            f"Started:     {started_str}",
            f"Finished:    {finished_str}",
            f"Duration:    {duration_str}",
            "------------------------------------------------------------",
            "Results by Test:",
        ]

        if report.results:
            for result in report.results:
                status_label = result.status.upper().ljust(5)
                res_duration = f"{result.duration_seconds:.2f}s"
                lines.append(f"  [{status_label}] {result.test_id} ({res_duration})")
                if result.error_message:
                    lines.append(f"          Error: {result.error_message}")
                for v in result.verification_results:
                    if not v.passed:
                        lines.append(
                            f"          Verification failed [{v.expectation.type}]: {v.message}"
                        )
        else:
            lines.append("  (No test results)")

        lines.extend(
            [
                "------------------------------------------------------------",
                f"Total:       {summary.total}",
                f"Passed:      {summary.passed}",
                f"Failed:      {summary.failed}",
                f"Errors:      {summary.errors}",
                f"Skipped:     {summary.skipped}",
                f"Pass Rate:   {pass_rate_pct}",
                "============================================================",
            ]
        )

        return "\n".join(lines)

    @classmethod
    def load(cls, file_path: Path | str) -> TestRunReport:
        """Load a TestRunReport from a saved JSON file.

        Args:
            file_path: Path to the JSON report file.

        Returns:
            TestRunReport: Deserialized report object.
        """
        path = Path(file_path)
        content = path.read_text(encoding="utf-8")
        return TestRunReport.model_validate_json(content)


def _package_version(package: str) -> str:
    """Return an installed package version without making reporting fragile."""
    try:
        return importlib.metadata.version(package)
    except importlib.metadata.PackageNotFoundError:
        return "unknown"
