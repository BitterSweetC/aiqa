"""AIQA Automated Failure Root-Cause Diagnostic Engine."""

from __future__ import annotations

import json
import logging
import os
import re
from typing import Any

from aiqa.models.test_case import FailureDiagnosis, TestCase, TestResult

logger = logging.getLogger(__name__)

DIAGNOSIS_PROMPT_TEMPLATE = """You are an expert QA Software Engineer and Root Cause Analyst.
A web test case failed or encountered an error. Analyze the provided test execution telemetry,
including verification failures, network errors, browser console errors, and page state.

Test Details:
- Test ID: {test_id}
- Test Name: {test_name}
- Start URL: {start_url}
- Goal: {goal}
- Error Message: {error_message}

Verification Checks:
{verifications}

Network Errors (4xx/5xx/failed requests):
{network_errors}

Browser Console Logs (errors/warnings):
{console_logs}

Current Page State:
- URL: {current_url}
- Title: {current_title}
- Text Snapshot: {body_text}

Provide a concise, highly actionable root cause analysis in valid JSON format:
{{
  "summary": "1-2 sentence high-level overview of why the test failed",
  "likely_cause": "Specific root cause category (e.g. 'Backend API 500 Internal Server Error', 'Missing DOM Selector', 'Unhandled JS Exception')",
  "evidence": [
    "Concrete evidence point 1",
    "Concrete evidence point 2"
  ],
  "remediation": "Clear, actionable developer guidance on how to reproduce and fix this issue",
  "severity": "critical" | "high" | "medium" | "low"
}}

Respond with ONLY valid JSON:
"""


class FailureAnalyzer:
    """Diagnoses test failures by synthesizing verification errors, DOM state, console errors, and network traffic."""

    def __init__(
        self,
        api_key: str | None = None,
        model: str | None = None,
        base_url: str | None = None,
    ) -> None:
        self.api_key = (
            api_key
            or os.getenv("OPENAI_API_KEY")
            or os.getenv("TEXT_MODEL_API_KEY")
        )
        self.base_url = (
            base_url
            or os.getenv("OPENAI_BASE_URL")
            or os.getenv("TEXT_MODEL_BASE_URL")
        )
        self.model = (
            model
            or os.getenv("OPENAI_MODEL")
            or os.getenv("TEXT_MODEL")
            or "gpt-4o-mini"
        )

    def diagnose(
        self,
        test: TestCase,
        result: TestResult,
        page_state: dict[str, Any] | None = None,
    ) -> FailureDiagnosis | None:
        """Analyze test failure and return structured diagnosis.

        Args:
            test: The executed TestCase.
            result: The resulting TestResult (failed or errored).
            page_state: Optional dictionary containing url, title, and body text.

        Returns:
            FailureDiagnosis object, or None if test passed/skipped.
        """
        if result.status not in ("fail", "error"):
            return None

        # 1. Attempt LLM-driven diagnosis if API key is provided
        if self.api_key:
            try:
                llm_diag = self._diagnose_with_llm(test, result, page_state)
                if llm_diag:
                    return llm_diag
            except Exception as exc:  # noqa: BLE001
                logger.warning("LLM failure diagnosis failed (%s). Falling back to heuristic diagnosis.", exc)

        # 2. Deterministic / Heuristic Root-Cause Diagnosis
        return self._diagnose_with_heuristics(test, result, page_state)

    def _diagnose_with_heuristics(
        self,
        test: TestCase,
        result: TestResult,
        page_state: dict[str, Any] | None = None,
    ) -> FailureDiagnosis:
        """Rule-based heuristic failure diagnosis without requiring external API keys."""
        network_errors = result.network_errors or []
        console_logs = result.console_logs or []
        failed_vrs = [vr for vr in result.verification_results if not vr.passed]

        # Case 1: Backend Server 5xx Error
        server_errs = [e for e in network_errors if e.get("status", 0) >= 500]
        if server_errs:
            top_err = server_errs[0]
            status_code = top_err.get("status", 500)
            url = top_err.get("url", "")
            method = top_err.get("method", "GET")
            return FailureDiagnosis(
                summary=f"Test failed due to a backend server error ({status_code}) on {url}.",
                likely_cause=f"Backend API Failure (HTTP {status_code} on {method} {url})",
                evidence=[
                    f"HTTP {status_code} received from endpoint: {url}",
                    f"Request method: {method}",
                ] + [f"Failed verification: {v.message}" for v in failed_vrs],
                remediation="Inspect backend application server logs for unhandled exceptions or service crashes on this endpoint.",
                severity="critical",
            )

        # Case 2: Authentication / Permission 401 or 403 Error
        auth_errs = [e for e in network_errors if e.get("status") in (401, 403)]
        if auth_errs:
            top_err = auth_errs[0]
            status_code = top_err.get("status", 401)
            url = top_err.get("url", "")
            return FailureDiagnosis(
                summary=f"Test failed due to authentication or permission rejection ({status_code}) on {url}.",
                likely_cause=f"Authentication / Access Denied (HTTP {status_code})",
                evidence=[
                    f"Endpoint returned HTTP {status_code} ({top_err.get('status_text', 'Unauthorized')}): {url}",
                ] + [f"Failed verification: {v.message}" for v in failed_vrs],
                remediation="Verify user authentication state or session credentials before executing this test case.",
                severity="high",
            )

        # Case 3: Frontend Uncaught JavaScript Exception
        js_errs = [c for c in console_logs if c.get("type") in ("error", "uncaught_error")]
        if js_errs:
            top_js = js_errs[0]
            err_text = top_js.get("text", "")
            return FailureDiagnosis(
                summary=f"Test failed in presence of browser console JavaScript errors: {err_text[:80]}.",
                likely_cause="Frontend JavaScript Runtime Error",
                evidence=[
                    f"Console error: {c.get('text', '')}" for c in js_errs[:3]
                ] + [f"Failed verification: {v.message}" for v in failed_vrs],
                remediation="Investigate browser console stack trace and fix the uncaught JavaScript exception in frontend code.",
                severity="high",
            )

        # Case 4: DOM Element Missing or State Mismatch
        dom_failures = [v for v in failed_vrs if v.expectation.type == "dom"]
        if dom_failures:
            top_dom = dom_failures[0]
            selector = top_dom.expectation.selector or "target element"
            return FailureDiagnosis(
                summary=f"DOM verification failed: {top_dom.message}",
                likely_cause=f"Element Missing or State Mismatch for selector '{selector}'",
                evidence=[
                    f"Failed check: {top_dom.message}",
                    f"Expected value: '{top_dom.expectation.value}'" if top_dom.expectation.value else f"Expected selector '{selector}' to match",
                    f"Observed actual value: '{top_dom.actual_value}'",
                ],
                remediation=f"Verify whether selector '{selector}' exists, is rendered conditionally, or requires waiting for asynchronous data fetch.",
                severity="medium",
            )

        # Case 5: URL / Route Mismatch
        url_failures = [v for v in failed_vrs if v.expectation.type == "url"]
        if url_failures:
            top_url = url_failures[0]
            return FailureDiagnosis(
                summary=f"URL expectation failed: {top_url.message}",
                likely_cause="Route or Navigation Mismatch",
                evidence=[
                    f"Check message: {top_url.message}",
                    f"Expected value: '{top_url.expectation.value}'",
                    f"Actual URL: '{top_url.actual_value}'",
                ],
                remediation="Check router configuration, client-side redirect rules, or navigation link targets.",
                severity="medium",
            )

        # Case 6: Execution Error / Timeout
        if result.status == "error" or result.error_message:
            return FailureDiagnosis(
                summary=f"Test execution encountered an error: {result.error_message or 'Unknown error'}",
                likely_cause="Browser Action Execution Error",
                evidence=[
                    f"Error details: {result.error_message or 'Action execution failed'}",
                    f"Action steps completed: {len(result.jev_steps)}",
                ],
                remediation="Check goal instructions, element interactability, and ensure network timeout is sufficiently configured.",
                severity="high",
            )

        # Fallback General Diagnosis
        return FailureDiagnosis(
            summary=f"Test '{test.id}' failed expectation verifications.",
            likely_cause="Expectation Verification Failure",
            evidence=[v.message for v in failed_vrs] or ["Expectations not satisfied"],
            remediation="Review test assertions and compare expected values with current page behavior.",
            severity="medium",
        )

    def _diagnose_with_llm(
        self,
        test: TestCase,
        result: TestResult,
        page_state: dict[str, Any] | None = None,
    ) -> FailureDiagnosis | None:
        """Call LLM to perform deep diagnostic reasoning."""
        import openai

        client_kwargs: dict[str, Any] = {"api_key": self.api_key}
        if self.base_url:
            client_kwargs["base_url"] = self.base_url
        client = openai.OpenAI(**client_kwargs)

        p_state = page_state or {}
        verifications_summary = "\n".join(
            f"- [{v.expectation.type}] {v.expectation.description} -> {'PASS' if v.passed else 'FAIL'}: {v.message}"
            for v in result.verification_results
        ) or "None"

        net_summary = "\n".join(
            f"- {e.get('method', 'GET')} {e.get('url', '')} -> HTTP {e.get('status', 0)} ({e.get('status_text', '')})"
            for e in (result.network_errors or [])
        ) or "None"

        console_summary = "\n".join(
            f"- [{c.get('type', 'log')}] {c.get('text', '')}"
            for c in (result.console_logs or [])
        ) or "None"

        body_preview = (p_state.get("text") or "")[:400].strip()

        prompt = DIAGNOSIS_PROMPT_TEMPLATE.format(
            test_id=test.id,
            test_name=test.name,
            start_url=test.start_url,
            goal=test.goal,
            error_message=result.error_message or "None",
            verifications=verifications_summary,
            network_errors=net_summary,
            console_logs=console_summary,
            current_url=p_state.get("url", test.start_url),
            current_title=p_state.get("title", ""),
            body_text=body_preview,
        )

        response = client.chat.completions.create(
            model=self.model,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.1,
        )

        raw = response.choices[0].message.content or ""
        clean = re.sub(r"```(?:json)?\s*([\s\S]*?)\s*```", r"\1", raw).strip()
        data = json.loads(clean)
        return FailureDiagnosis.model_validate(data)
