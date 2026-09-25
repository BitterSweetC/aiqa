"""Structured audit logging for AIQA enterprise test runs and lifecycle operations."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from aiqa.models.test_case import TestRunReport
from aiqa.security.redaction import redact_data


class AuditLogger:
    """Writes redacted, tamper-evident JSONL audit records for AIQA runs."""

    def __init__(self, log_path: Path | str) -> None:
        self.log_path = Path(log_path)

    def log_event(
        self,
        event_type: str,
        details: dict[str, Any] | None = None,
        *,
        actor: str | None = None,
        role: str | None = None,
        test_id: str | None = None,
        status: str = "ok",
    ) -> dict[str, Any]:
        """Append a single redacted audit event to the JSONL audit log."""
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        record = redact_data(
            {
                "timestamp": datetime.now(UTC).isoformat(),
                "event_type": event_type,
                "actor": actor or "aiqa-runner",
                "role": role,
                "test_id": test_id,
                "status": status,
                "details": details or {},
            }
        )
        with self.log_path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, sort_keys=True) + "\n")
        return record

    def log_suite_run(
        self,
        report: TestRunReport,
        *,
        owner: str | None = None,
    ) -> list[dict[str, Any]]:
        """Record suite execution and any fixture/policy events into the audit log."""
        records: list[dict[str, Any]] = []
        records.append(
            self.log_event(
                "suite_run",
                {
                    "run_id": report.run_id,
                    "suite_name": report.suite_name,
                    "base_url": report.base_url,
                    "total": report.summary.total,
                    "passed": report.summary.passed,
                    "failed": report.summary.failed,
                    "errors": report.summary.errors,
                    "flaky": report.summary.flaky,
                    "cleanup_failures": report.summary.cleanup_failures,
                },
                actor=owner or "aiqa-ci",
                status="pass" if (report.summary.failed == 0 and report.summary.errors == 0) else "fail",
            )
        )
        for res in report.results:
            if res.failure_category == "policy_violation":
                records.append(
                    self.log_event(
                        "policy_violation",
                        {"error_message": res.error_message},
                        actor=owner or "aiqa-ci",
                        test_id=res.test_id,
                        status="blocked",
                    )
                )
            for entity in res.created_entities:
                records.append(
                    self.log_event(
                        "fixture_entity",
                        {
                            "fixture_name": entity.fixture_name,
                            "entity_id": entity.entity_id,
                            "cleaned_up": entity.cleaned_up,
                            "cleanup_error": entity.cleanup_error,
                        },
                        actor=owner or "aiqa-ci",
                        test_id=res.test_id,
                        status="cleaned" if entity.cleaned_up else "orphaned",
                    )
                )
        return records

    def read_events(self) -> list[dict[str, Any]]:
        """Read all JSONL audit events from disk."""
        if not self.log_path.exists():
            return []
        events: list[dict[str, Any]] = []
        for line in self.log_path.read_text(encoding="utf-8").splitlines():
            line_str = line.strip()
            if line_str:
                events.append(json.loads(line_str))
        return events


__all__ = ["AuditLogger"]
