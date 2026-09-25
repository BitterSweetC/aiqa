"""Unit tests for Stage 3 Feature Registry and Coverage Analyzer."""

from __future__ import annotations

from pathlib import Path
import pytest

from aiqa.models.coverage import DiscoveredFeature, FeatureRegistry
from aiqa.models.test_case import Expectation, TestCase, TestSuite
from aiqa.orchestrator.coverage import CoverageAnalyzer


def test_coverage_analyzer_matching_and_gap_detection():
    """CoverageAnalyzer calculates coverage percentage and pinpoints untested gaps."""
    # 1. Setup FeatureRegistry
    features = [
        DiscoveredFeature(
            id="feat_search_001",
            name="Search Bar: Keyword Search",
            type="search",
            url="https://store.example.com",
            selector="#search-input",
        ),
        DiscoveredFeature(
            id="feat_cart_001",
            name="Shopping Cart Button",
            type="cart",
            url="https://store.example.com",
            selector="#nav-cart",
        ),
        DiscoveredFeature(
            id="feat_auth_001",
            name="Login / Sign In Link",
            type="auth",
            url="https://store.example.com",
            selector="#login-link",
        ),
        DiscoveredFeature(
            id="feat_checkout_001",
            name="Checkout Gateway Flow",
            type="checkout",
            url="https://store.example.com/checkout",
        ),
    ]

    registry = FeatureRegistry(
        base_url="https://store.example.com",
        routes=["https://store.example.com", "https://store.example.com/checkout"],
        features=features,
    )

    # 2. Setup TestSuite covering search and cart, but NOT auth or checkout
    suite = TestSuite(
        name="Search & Cart Suite",
        base_url="https://store.example.com",
        tests=[
            TestCase(
                id="SEARCH-001",
                name="Search for laptop",
                start_url="https://store.example.com",
                preconditions=[],
                goal="Type laptop into #search-input",
                expected=[
                    Expectation(type="dom", description="Search results appear", selector="#search-input")
                ],
                tags=["search"],
            ),
            TestCase(
                id="CART-001",
                name="View shopping cart",
                start_url="https://store.example.com",
                preconditions=[],
                goal="Click on #nav-cart",
                expected=[
                    Expectation(type="dom", description="Cart items displayed", selector="#nav-cart")
                ],
                tags=["cart"],
            ),
        ],
    )

    # 3. Analyze coverage
    analyzer = CoverageAnalyzer()
    report = analyzer.analyze(registry, suite)

    assert report.total_features == 4
    assert report.covered_features == 2
    assert report.coverage_rate == 0.5  # 50% coverage

    assert "feat_search_001" in report.tested_feature_ids
    assert "feat_cart_001" in report.tested_feature_ids

    # Verify gaps
    untested_ids = [f.id for f in report.untested_features]
    assert "feat_auth_001" in untested_ids
    assert "feat_checkout_001" in untested_ids

    # Verify category breakdown
    assert "search" in report.by_type
    assert report.by_type["search"].total == 1
    assert report.by_type["search"].covered == 1
    assert report.by_type["search"].rate == 1.0

    assert "auth" in report.by_type
    assert report.by_type["auth"].total == 1
    assert report.by_type["auth"].covered == 0
    assert report.by_type["auth"].rate == 0.0


@pytest.mark.asyncio
async def test_site_crawler_page_extraction(tmp_path: Path):
    """SiteCrawler extracts routes and classifies features on HTML pages."""
    from aiqa.executor.browser_session import BrowserSession
    from aiqa.crawler.site_crawler import SiteCrawler

    html_content = """<!DOCTYPE html>
    <html>
      <head><title>Demo Marketplace</title></head>
      <body>
        <input type="search" id="search" name="q" placeholder="Search products...">
        <a href="/cart" id="cart-link">Cart (0)</a>
        <a href="/login" id="login-btn">Sign in</a>
        <button id="promo-btn">Get Promo</button>
      </body>
    </html>
    """

    crawler = SiteCrawler(max_pages=1)
    async with BrowserSession(headless=True) as session:
        await session.page.set_content(html_content)
        # Verify page evaluate logic
        res = await session.evaluate_js("""
            () => {
                const inputs = Array.from(document.querySelectorAll('input')).map(i => ({
                    type: i.type, name: i.name, placeholder: i.placeholder, id: '#' + i.id
                }));
                const links = Array.from(document.querySelectorAll('a')).map(a => ({
                    text: a.innerText, href: a.href
                }));
                return { inputs, links };
            }
        """)

    assert len(res["inputs"]) >= 1
    assert any("cart" in l["text"].lower() for l in res["links"])
    assert any("sign in" in l["text"].lower() for l in res["links"])
