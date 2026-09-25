"""Unit tests for AIQA verifiers (DomVerifier, UrlVerifier, SemanticVerifier)."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from aiqa.models.test_case import Expectation, VerificationResult
from aiqa.verifier.dom import DomVerifier
from aiqa.verifier.semantic import SemanticVerifier
from aiqa.verifier.url import UrlVerifier

# ---------------------------------------------------------
# Test DomVerifier
# ---------------------------------------------------------

@pytest.mark.asyncio
async def test_dom_verifier_element_exists():
    verifier = DomVerifier()
    exp = Expectation(
        type="dom",
        description="Element .product-card should exist",
        selector=".product-card, [data-testid='product-card']",
        value="exists",
    )

    mock_page = MagicMock()
    mock_el = MagicMock()
    mock_page.query_selector_all = AsyncMock(return_value=[mock_el])

    result = await verifier.verify(exp, mock_page)
    assert isinstance(result, VerificationResult)
    assert result.passed is True
    assert result.actual_value == "exists"
    assert "exists" in result.message


@pytest.mark.asyncio
async def test_dom_verifier_element_not_found():
    verifier = DomVerifier()
    exp = Expectation(
        type="dom",
        description="Element .nonexistent should exist",
        selector=".nonexistent",
        value="exists",
    )

    mock_page = MagicMock()
    mock_page.query_selector_all = AsyncMock(return_value=[])
    mock_page.query_selector = AsyncMock(return_value=None)

    result = await verifier.verify(exp, mock_page)
    assert result.passed is False
    assert result.actual_value == "not_found"


@pytest.mark.asyncio
async def test_dom_verifier_element_absence_negative_check():
    verifier = DomVerifier()
    exp = Expectation(
        type="dom",
        description="Element .empty-cart should not exist",
        selector=".empty-cart",
        value="not_exists",
    )

    mock_page = MagicMock()
    mock_page.query_selector_all = AsyncMock(return_value=[])

    result = await verifier.verify(exp, mock_page)
    assert result.passed is True
    assert result.actual_value == "absent"


@pytest.mark.asyncio
async def test_dom_verifier_cart_count_text():
    verifier = DomVerifier()
    exp = Expectation(
        type="dom",
        description="Cart count should equal 1",
        selector="[data-testid='cart-count'], .cart-count",
        value="1",
    )

    mock_page = MagicMock()
    mock_el = MagicMock()
    mock_el.text_content = AsyncMock(return_value=" 1 ")
    mock_page.query_selector_all = AsyncMock(return_value=[mock_el])

    result = await verifier.verify(exp, mock_page)
    assert result.passed is True
    assert result.actual_value == "1"


@pytest.mark.asyncio
async def test_dom_verifier_cart_count_element_length():
    verifier = DomVerifier()
    exp = Expectation(
        type="dom",
        description="Cart count should equal 2",
        selector=".cart-item",
        value="2",
    )

    mock_page = MagicMock()
    el1 = MagicMock()
    el1.text_content = AsyncMock(return_value="Item A")
    el2 = MagicMock()
    el2.text_content = AsyncMock(return_value="Item B")
    mock_page.query_selector_all = AsyncMock(return_value=[el1, el2])

    result = await verifier.verify(exp, mock_page)
    assert result.passed is True
    assert result.actual_value == "2"


@pytest.mark.asyncio
async def test_dom_verifier_page_title():
    verifier = DomVerifier()
    exp = Expectation(
        type="dom",
        description="Page title should contain 'Products'",
        value="Products",
    )

    mock_page = MagicMock()
    mock_page.title = AsyncMock(return_value="Acme Store — Products Listing")

    result = await verifier.verify(exp, mock_page)
    assert result.passed is True
    assert result.actual_value == "Acme Store — Products Listing"


@pytest.mark.asyncio
async def test_dom_verifier_attribute():
    verifier = DomVerifier()
    exp = Expectation(
        type="dom",
        description="Element button should have attribute disabled='true'",
        selector="button#checkout",
        value="true",
    )

    mock_page = MagicMock()
    mock_el = MagicMock()
    mock_el.get_attribute = AsyncMock(return_value="true")
    mock_page.query_selector_all = AsyncMock(return_value=[mock_el])

    result = await verifier.verify(exp, mock_page)
    assert result.passed is True
    assert result.actual_value == "true"


# ---------------------------------------------------------
# Test UrlVerifier
# ---------------------------------------------------------

@pytest.mark.asyncio
async def test_url_verifier_contains_path():
    verifier = UrlVerifier()
    exp = Expectation(
        type="url",
        description="URL should contain '/product'",
        value="/product",
    )

    mock_page = MagicMock()
    mock_page.url = "http://localhost:3000/product/monitor-4k"

    result = await verifier.verify(exp, mock_page)
    assert result.passed is True
    assert "http://localhost:3000/product/monitor-4k" in result.actual_value


@pytest.mark.asyncio
async def test_url_verifier_contains_query():
    verifier = UrlVerifier()
    exp = Expectation(
        type="url",
        description="URL should contain 'monitor'",
        value="monitor",
    )

    mock_page = MagicMock()
    mock_page.url = "http://localhost:3000/search?q=monitor"

    result = await verifier.verify(exp, mock_page)
    assert result.passed is True
    assert result.actual_value == "http://localhost:3000/search?q=monitor"


@pytest.mark.asyncio
async def test_url_verifier_regex_pattern():
    verifier = UrlVerifier()
    exp = Expectation(
        type="url",
        description="URL should match pattern '^http://localhost:3000/orders/\\d+$'",
        value=r"^http://localhost:3000/orders/\d+$",
    )

    mock_page = MagicMock()
    mock_page.url = "http://localhost:3000/orders/12345"

    result = await verifier.verify(exp, mock_page)
    assert result.passed is True


@pytest.mark.asyncio
async def test_url_verifier_title():
    verifier = UrlVerifier()
    exp = Expectation(
        type="url",
        description="Page title should contain 'Products'",
        value="Products",
    )

    mock_page = MagicMock()
    mock_page.url = "http://localhost:3000/products"
    mock_page.title = AsyncMock(return_value="Online Store - Products")

    result = await verifier.verify(exp, mock_page)
    assert result.passed is True
    assert result.actual_value == "Online Store - Products"


# ---------------------------------------------------------
# Test SemanticVerifier
# ---------------------------------------------------------

@pytest.mark.asyncio
async def test_semantic_verifier_missing_api_key(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    verifier = SemanticVerifier(api_key=None)
    exp = Expectation(
        type="semantic",
        description="The selected monitor should appear in the cart",
    )

    page_state = {
        "url": "http://localhost:3000/cart",
        "title": "Your Cart",
        "text": "UltraSharp 4K Monitor - Quantity: 1",
    }

    result = await verifier.verify(exp, page_state)
    assert result.passed is False
    assert "LLM verification requires an API key" in result.message


@pytest.mark.asyncio
async def test_semantic_verifier_with_mocked_llm(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "test-fake-key")
    verifier = SemanticVerifier(api_key="test-fake-key")
    exp = Expectation(
        type="semantic",
        description="The selected monitor should appear in the cart",
    )

    mock_client = MagicMock()
    mock_choice = MagicMock()
    mock_choice.message.content = (
        '{"passed": true, "actual_value": "UltraSharp 4K Monitor visible in cart", '
        '"reason": "The monitor item is clearly rendered with quantity 1."}'
    )
    mock_client.chat.completions.create = AsyncMock(
        return_value=MagicMock(choices=[mock_choice])
    )

    with patch.object(verifier, "_get_client", return_value=mock_client):
        page_state = {
            "url": "http://localhost:3000/cart",
            "title": "Your Cart",
            "text": "UltraSharp 4K Monitor - Quantity: 1",
        }
        result = await verifier.verify(exp, page_state)
        assert result.passed is True
        assert result.actual_value == "UltraSharp 4K Monitor visible in cart"
        assert "clearly rendered" in result.message


@pytest.mark.asyncio
async def test_shopping_site_sample_expectations(monkeypatch):
    """Test all expectations defined in sample_tests/shopping_site.json."""
    from aiqa.models.test_case import load_test_suite

    suite = load_test_suite("sample_tests/shopping_site.json")
    assert len(suite.tests) == 5

    dom_verifier = DomVerifier()
    url_verifier = UrlVerifier()
    semantic_verifier = SemanticVerifier(api_key="mock-key")

    mock_client = MagicMock()
    mock_choice = MagicMock()
    mock_choice.message.content = (
        '{"passed": true, "actual_value": "Expected content displayed", '
        '"reason": "Verified successfully"}'
    )
    mock_client.chat.completions.create = AsyncMock(
        return_value=MagicMock(choices=[mock_choice])
    )

    with patch.object(semantic_verifier, "_get_client", return_value=mock_client):
        for test in suite.tests:
            for exp in test.expected:
                mock_page = MagicMock()
                mock_el = MagicMock()
                mock_el.text_content = AsyncMock(return_value=exp.value or "1")
                mock_el.input_value = AsyncMock(return_value=exp.value or "1")
                mock_page.query_selector_all = AsyncMock(return_value=[mock_el])

                target_url = (
                    f"http://localhost:3000{exp.value}"
                    if exp.value and exp.value.startswith("/")
                    else f"http://localhost:3000/search?q={exp.value or ''}"
                )
                mock_page.url = target_url
                mock_page.title = AsyncMock(return_value="Shopping Site")

                if exp.type == "dom":
                    res = await dom_verifier.verify(exp, mock_page)
                    assert isinstance(res, VerificationResult)
                    assert res.passed is True, f"Failed DOM expectation: {exp}"
                elif exp.type == "url":
                    res = await url_verifier.verify(exp, mock_page)
                    assert isinstance(res, VerificationResult)
                    assert res.passed is True, f"Failed URL expectation: {exp}"
                elif exp.type == "semantic":
                    page_state = {
                        "url": mock_page.url,
                        "title": "Shopping Site",
                        "text": "Page text",
                    }
                    res = await semantic_verifier.verify(exp, page_state)
                    assert isinstance(res, VerificationResult)
                    assert res.passed is True, f"Failed Semantic expectation: {exp}"

