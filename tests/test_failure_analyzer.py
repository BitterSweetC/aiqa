"""Unit tests for Stage 5 Failure Diagnosis Engine."""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import MagicMock, patch

from aiqa.analyzer.failure_analyzer import FailureAnalyzer
from aiqa.models.test_case import (
    Expectation,
    FailureDiagnosis,
    TestCase,
    TestResult,
    VerificationResult,
)


def _make_test_case(test_id: str = "CART-001") -> TestCase:
    return TestCase(
        id=test_id,
        name="Add to cart test",
        start_url="https://shop.example.com",
        preconditions=[],
        goal="Click button 'Add to cart'",
        expected=[
            Expectation(type="dom", description="Cart badge is 1", selector=".cart-count", value="1"),
            Expectation(type="url", description="Navigated to cart", value="/cart"),
        ],
    )


def test_failure_analyzer_backend_500_diagnosis():
    """FailureAnalyzer pinpoints backend 500 server error when 5xx network error is present."""
    test = _make_test_case()
    vr = VerificationResult(
        expectation=test.expected[0],
        passed=False,
        actual_value=None,
        message="Element .cart-count not found",
    )
    result = TestResult(
        test_id=test.id,
        status="fail",
        duration_seconds=2.1,
        jev_steps=[{"action": "click", "status": "completed"}],
        verification_results=[vr],
        timestamp=datetime.now(UTC),
        network_errors=[
            {
                "url": "https://shop.example.com/api/cart",
                "status": 500,
                "status_text": "Internal Server Error",
                "method": "POST",
            }
        ],
        console_logs=[],
    )

    analyzer = FailureAnalyzer()
    diag = analyzer.diagnose(test, result)

    assert diag is not None
    assert isinstance(diag, FailureDiagnosis)
    assert diag.severity == "critical"
    assert "Backend API Failure" in diag.likely_cause
    assert "500" in diag.likely_cause
    assert any("500" in e for e in diag.evidence)
    assert "backend application server logs" in diag.remediation.lower()


def test_failure_analyzer_frontend_js_error_diagnosis():
    """FailureAnalyzer detects uncaught frontend JavaScript exceptions."""
    test = _make_test_case()
    vr = VerificationResult(
        expectation=test.expected[0],
        passed=False,
        actual_value=None,
        message="Element .cart-count not found",
    )
    result = TestResult(
        test_id=test.id,
        status="fail",
        duration_seconds=1.5,
        jev_steps=[],
        verification_results=[vr],
        timestamp=datetime.now(UTC),
        network_errors=[],
        console_logs=[
            {
                "type": "error",
                "text": "Uncaught TypeError: Cannot read properties of undefined (reading 'addToCart')",
            }
        ],
    )

    analyzer = FailureAnalyzer()
    diag = analyzer.diagnose(test, result)

    assert diag is not None
    assert diag.severity == "high"
    assert "Frontend JavaScript Runtime Error" in diag.likely_cause
    assert any("TypeError" in e for e in diag.evidence)
    assert "console" in diag.remediation.lower()


def test_failure_analyzer_dom_missing_selector_diagnosis():
    """FailureAnalyzer pinpoints missing DOM selector when no network/JS errors occur."""
    test = _make_test_case()
    vr = VerificationResult(
        expectation=test.expected[0],
        passed=False,
        actual_value="0",
        message="Expected value '1' but got '0'",
    )
    result = TestResult(
        test_id=test.id,
        status="fail",
        duration_seconds=1.2,
        jev_steps=[],
        verification_results=[vr],
        timestamp=datetime.now(UTC),
        network_errors=[],
        console_logs=[],
    )

    analyzer = FailureAnalyzer()
    diag = analyzer.diagnose(test, result)

    assert diag is not None
    assert "Element Missing or State Mismatch" in diag.likely_cause
    assert ".cart-count" in diag.likely_cause
    assert any("Expected value: '1'" in e for e in diag.evidence)


def test_failure_analyzer_url_mismatch_diagnosis():
    """FailureAnalyzer diagnoses URL route mismatch."""
    test = _make_test_case()
    vr = VerificationResult(
        expectation=test.expected[1],
        passed=False,
        actual_value="https://shop.example.com/products",
        message="URL does not contain '/cart'",
    )
    result = TestResult(
        test_id=test.id,
        status="fail",
        duration_seconds=1.0,
        jev_steps=[],
        verification_results=[vr],
        timestamp=datetime.now(UTC),
        network_errors=[],
        console_logs=[],
    )

    analyzer = FailureAnalyzer()
    diag = analyzer.diagnose(test, result)

    assert diag is not None
    assert "Route or Navigation Mismatch" in diag.likely_cause
    assert any("Actual URL" in e for e in diag.evidence)


def test_failure_analyzer_passed_test_returns_none():
    """FailureAnalyzer returns None for passed tests."""
    test = _make_test_case()
    vr = VerificationResult(expectation=test.expected[0], passed=True, message="Cart count verified")
    result = TestResult(
        test_id=test.id,
        status="pass",
        duration_seconds=1.0,
        jev_steps=[],
        verification_results=[vr],
        timestamp=datetime.now(UTC),
    )

    analyzer = FailureAnalyzer()
    diag = analyzer.diagnose(test, result)
    assert diag is None


def test_failure_analyzer_mocked_llm_diagnosis():
    """FailureAnalyzer properly invokes LLM mode when API key is provided."""
    test = _make_test_case()
    result = TestResult(
        test_id=test.id,
        status="fail",
        duration_seconds=2.0,
        jev_steps=[],
        verification_results=[],
        timestamp=datetime.now(UTC),
    )

    mock_llm_response = {
        "summary": "Checkout API crashed due to invalid currency code.",
        "likely_cause": "Invalid Currency Parameter",
        "evidence": ["HTTP 400 from /api/checkout"],
        "remediation": "Validate currency format in frontend before submitting.",
        "severity": "high",
    }

    mock_completion = MagicMock()
    mock_completion.choices = [
        MagicMock(message=MagicMock(content=f"```json\n{MagicMock(return_value=mock_llm_response)}\n```"))
    ]
    mock_completion.choices[0].message.content = f"""```json
    {{
      "summary": "Checkout API crashed due to invalid currency code.",
      "likely_cause": "Invalid Currency Parameter",
      "evidence": ["HTTP 400 from /api/checkout"],
      "remediation": "Validate currency format in frontend before submitting.",
      "severity": "high"
    }}
    ```"""

    with patch("openai.OpenAI") as mock_openai:
        mock_client = MagicMock()
        mock_client.chat.completions.create.return_value = mock_completion
        mock_openai.return_value = mock_client

        analyzer = FailureAnalyzer(api_key="sk-test-key")
        diag = analyzer.diagnose(test, result)

        assert diag is not None
        assert diag.likely_cause == "Invalid Currency Parameter"
        assert diag.severity == "high"
        assert "currency format" in diag.remediation
