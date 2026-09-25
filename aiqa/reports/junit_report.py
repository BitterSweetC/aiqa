"""JUnit XML report generation for CI systems."""

from __future__ import annotations

import json
import xml.etree.ElementTree as ET
from pathlib import Path

from aiqa.models.test_case import TestResult, TestRunReport
from aiqa.reports.json_report import REPORT_SCHEMA_VERSION
from aiqa.security.redaction import redact_data, redact_text


class JUnitReporter:
    """Serialize an AIQA run as portable JUnit XML."""

    def __init__(self, output_dir: Path | str = Path("./reports")) -> None:
        self.output_dir = Path(output_dir)

    def save(
        self,
        report: TestRunReport,
        filename: str | Path | None = None,
    ) -> Path:
        """Write a JUnit report and return its path."""
        self.output_dir.mkdir(parents=True, exist_ok=True)
        if filename is None:
            timestamp = report.started_at.strftime("%Y%m%d_%H%M%S")
            target_path = self.output_dir / f"junit_{timestamp}.xml"
        else:
            target_path = Path(filename)
            if not target_path.is_absolute() and target_path.parent == Path("."):
                target_path = self.output_dir / target_path

        target_path.parent.mkdir(parents=True, exist_ok=True)
        tree = ET.ElementTree(self.generate(report))
        ET.indent(tree, space="  ")
        tree.write(target_path, encoding="utf-8", xml_declaration=True)
        return target_path

    def generate(self, report: TestRunReport) -> ET.Element:
        """Build a JUnit XML tree for a test run."""
        common_attributes = {
            "name": redact_text(report.suite_name),
            "tests": str(report.summary.total),
            "failures": str(report.summary.failed),
            "errors": str(report.summary.errors),
            "skipped": str(report.summary.skipped),
            "time": f"{report.summary.duration_seconds:.6f}",
        }
        testsuites = ET.Element("testsuites", common_attributes)
        suite = ET.SubElement(
            testsuites,
            "testsuite",
            {**common_attributes, "timestamp": report.started_at.isoformat()},
        )
        properties = ET.SubElement(suite, "properties")
        ET.SubElement(properties, "property", {"name": "aiqa.run_id", "value": report.run_id})
        ET.SubElement(
            properties,
            "property",
            {"name": "aiqa.base_url", "value": redact_text(report.base_url)},
        )
        ET.SubElement(
            properties,
            "property",
            {"name": "aiqa.schema_version", "value": REPORT_SCHEMA_VERSION},
        )
        ET.SubElement(
            properties,
            "property",
            {"name": "aiqa.flaky_count", "value": str(getattr(report.summary, "flaky", 0))},
        )

        for result in report.results:
            self._append_test_case(suite, report.suite_name, result)
        return testsuites

    @staticmethod
    def _append_test_case(parent: ET.Element, suite_name: str, result: TestResult) -> None:
        case = ET.SubElement(
            parent,
            "testcase",
            {
                "name": result.test_id,
                "classname": redact_text(suite_name),
                "time": f"{result.duration_seconds:.6f}",
                "timestamp": result.timestamp.isoformat(),
            },
        )
        message = redact_text(result.error_message or _default_status_message(result.status))
        if result.status == "fail":
            failure = ET.SubElement(case, "failure", {"message": message, "type": "assertion"})
            failure.text = message
        elif result.status == "error":
            error = ET.SubElement(case, "error", {"message": message, "type": "execution"})
            error.text = message
        elif result.status == "skip":
            ET.SubElement(case, "skipped", {"message": message})

        evidence = redact_data(
            {
                "action_outcomes": result.jev_steps,
                "verification_evidence": [
                    verification.model_dump(mode="json")
                    for verification in result.verification_results
                ],
                "attempts": [
                    attempt.model_dump(mode="json")
                    for attempt in getattr(result, "attempts", [])
                ],
                "flaky": bool(getattr(result, "flaky", False)),
                "failure_category": getattr(result, "failure_category", None),
                "artifact_paths": (
                    {"screenshot": result.screenshot_path} if result.screenshot_path else {}
                ),
            }
        )
        ET.SubElement(case, "system-out").text = json.dumps(
            evidence,
            ensure_ascii=False,
            sort_keys=True,
        )


def _default_status_message(status: str) -> str:
    return {
        "fail": "One or more deterministic verifications failed",
        "error": "Test execution failed",
        "skip": "Test was skipped",
    }.get(status, "")
