"""Tests for closed-loop exploration, goal planning, DAG dependencies, coverage, budgets, and defect evaluation."""

from __future__ import annotations

import asyncio
import threading
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest
from playwright.async_api import async_playwright

from aiqa.benchmarks.harness import BenchmarkHarness, BuggyFixtureSiteServer
from aiqa.cli import _run_auto
from aiqa.crawler.site_crawler import SiteCrawler
from aiqa.executor.action_driver import ActionDriver
from aiqa.models.coverage import DiscoveredFeature, FeatureRegistry
from aiqa.models.test_case import (
    Expectation,
    TestCase,
    TestResult,
    TestRunReport,
    TestSuite,
)
from aiqa.orchestrator.coverage import CoverageAnalyzer
from aiqa.orchestrator.runner import TestRunner
from aiqa.planner.site_inspector import InspectedPage
from aiqa.planner.test_generator import TestPlanner, decompose_goal


class _MultiRouteTestHandler(BaseHTTPRequestHandler):
    """Local HTTP server with multi-route features, modal overlay, and protected admin route."""

    def log_message(self, format: str, *args: object) -> None:
        return

    def do_GET(self) -> None:
        path = self.path.split("?")[0]
        if path == "/":
            body = """<!doctype html>
<html>
<head><title>Store Home</title></head>
<body>
  <h1>Welcome to Store Home</h1>
  <nav>
    <a id="nav-catalog" href="/catalog">Catalog</a>
    <a id="nav-pricing" href="/pricing">Pricing &amp; Coupons</a>
    <a id="nav-admin" href="/admin">Admin Console</a>
  </nav>
</body>
</html>"""
            self._send(200, body)
        elif path == "/catalog":
            body = """<!doctype html>
<html>
<head><title>Product Catalog</title></head>
<body>
  <h1>Product Catalog</h1>
  <form id="catalog-form" action="/catalog" method="get">
    <input id="catalog-search" name="q" type="search" placeholder="Search catalog" />
    <button id="catalog-submit" type="submit">Search</button>
  </form>
  <div id="results">Showing catalog items</div>
</body>
</html>"""
            self._send(200, body)
        elif path == "/pricing":
            body = """<!doctype html>
<html>
<head><title>Pricing &amp; Discounts</title></head>
<body>
  <h1>Order Pricing</h1>
  <input id="coupon-input" name="coupon" type="text" placeholder="Enter coupon code" />
  <span id="subtotal">$100</span>
  <span id="discount">$15</span>
  <span id="total">$85</span>
</body>
</html>"""
            self._send(200, body)
        elif path == "/admin":
            self._send(
                403,
                "<html><head><title>403 Forbidden</title></head>"
                "<body><h1>403 Forbidden - Access Denied</h1></body></html>",
            )
        else:
            self._send(404, "<html><body>Not Found</body></html>")

    def _send(self, status: int, html: str) -> None:
        encoded = html.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)


@pytest.fixture()
def multi_route_server():
    server = ThreadingHTTPServer(("127.0.0.1", 0), _MultiRouteTestHandler)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{port}"
    finally:
        server.shutdown()
        server.server_close()


def test_multi_dependency_dag_grouping_and_topological_order() -> None:
    """Test C depending on both A and B is grouped into [A, B, C] in topological order."""
    runner = TestRunner(headless=True)
    test_a = TestCase(
        id="test_a",
        name="Parent A",
        start_url="http://127.0.0.1:8000/",
        actions=[{"action": "navigate", "url": "http://127.0.0.1:8000/"}],
        expected=[Expectation(type="url", description="Home", value="http://127.0.0.1:8000/")],
    )
    test_b = TestCase(
        id="test_b",
        name="Parent B",
        start_url="http://127.0.0.1:8000/",
        actions=[{"action": "navigate", "url": "http://127.0.0.1:8000/"}],
        expected=[Expectation(type="url", description="Home", value="http://127.0.0.1:8000/")],
    )
    test_c = TestCase(
        id="test_c",
        name="Child C",
        start_url="http://127.0.0.1:8000/catalog",
        depends_on=["test_a", "test_b"],
        preconditions=["Requires test_a", "Requires test_b"],
        actions=[{"action": "navigate", "url": "http://127.0.0.1:8000/catalog"}],
        expected=[Expectation(type="url", description="Catalog", value="/catalog")],
    )

    groups = runner._group_tests_by_dependency([test_c, test_b, test_a])
    assert len(groups) == 1
    group_ids = [t.id for t in groups[0]]
    assert group_ids.index("test_a") < group_ids.index("test_c")
    assert group_ids.index("test_b") < group_ids.index("test_c")


def test_multi_dependency_execution_skips_child_when_second_parent_fails(
    multi_route_server: str,
    tmp_path: Path,
) -> None:
    """When C depends on A (pass) and B (fail), C must be skipped in parallel mode."""
    suite = TestSuite(
        name="Multi-Parent Dependency Suite",
        base_url=multi_route_server,
        tests=[
            TestCase(
                id="parent_a",
                name="Parent A passes",
                start_url=f"{multi_route_server}/",
                actions=[{"action": "navigate", "url": f"{multi_route_server}/"}],
                expected=[
                    Expectation(type="dom", description="Home heading", selector="h1", value="Store Home")
                ],
            ),
            TestCase(
                id="parent_b",
                name="Parent B fails",
                start_url=f"{multi_route_server}/",
                actions=[{"action": "navigate", "url": f"{multi_route_server}/"}],
                expected=[
                    Expectation(
                        type="dom",
                        description="Non-existent text",
                        selector="h1",
                        value="NonExistentTextXYZ",
                    )
                ],
            ),
            TestCase(
                id="child_c",
                name="Child C depends on A and B",
                start_url=f"{multi_route_server}/catalog",
                depends_on=["parent_a", "parent_b"],
                actions=[{"action": "navigate", "url": f"{multi_route_server}/catalog"}],
                expected=[
                    Expectation(
                        type="dom",
                        description="Catalog heading",
                        selector="h1",
                        value="Product Catalog",
                    )
                ],
            ),
        ],
    )
    runner = TestRunner(headless=True, screenshots_dir=tmp_path / "screenshots", workers=2)
    report = asyncio.run(runner.run_suite(suite))
    by_id = {r.test_id: r for r in report.results}
    assert by_id["parent_a"].status == "pass"
    assert by_id["parent_b"].status == "fail"
    assert by_id["child_c"].status == "skip"
    assert "parent_b" in (by_id["child_c"].error_message or "")


def test_per_test_timeout_enforced(multi_route_server: str, tmp_path: Path) -> None:
    """TestCase.timeout is enforced via asyncio.wait_for and surfaces a clear timeout error."""
    suite = TestSuite(
        name="Timeout Enforcement Suite",
        base_url=multi_route_server,
        tests=[
            TestCase(
                id="slow_test",
                name="Test exceeding per-test timeout",
                start_url=f"{multi_route_server}/",
                timeout=1,
                actions=[
                    {"action": "navigate", "url": f"{multi_route_server}/"},
                    {"action": "wait", "timeout_ms": 5000},
                ],
                expected=[
                    Expectation(type="dom", description="Home heading", selector="h1", value="Store Home")
                ],
            )
        ],
    )
    runner = TestRunner(headless=True, screenshots_dir=tmp_path / "screenshots")
    report = asyncio.run(runner.run_suite(suite))
    result = report.results[0]
    assert result.status == "error"
    assert "timed out after 1" in (result.error_message or "")


def test_route_aware_and_execution_verified_coverage() -> None:
    """CoverageAnalyzer enforces route compatibility and separates planned vs execution-verified coverage."""
    registry = FeatureRegistry(
        base_url="http://127.0.0.1:8000",
        routes=["http://127.0.0.1:8000", "http://127.0.0.1:8000/catalog", "http://127.0.0.1:8000/pricing"],
        blocked_routes={"http://127.0.0.1:8000/admin": "HTTP 403"},
        features=[
            DiscoveredFeature(
                id="feat_home_nav",
                name="Navigation: Catalog",
                type="navigation",
                url="http://127.0.0.1:8000",
                selector="#nav-catalog",
            ),
            DiscoveredFeature(
                id="feat_catalog_search",
                name="Search Input: Search catalog",
                type="search",
                url="http://127.0.0.1:8000/catalog",
                selector="#catalog-search",
            ),
            DiscoveredFeature(
                id="feat_pricing_calc",
                name="Pricing & Discount Surface",
                type="pricing",
                url="http://127.0.0.1:8000/pricing",
                selector="#total",
            ),
        ],
    )

    suite = TestSuite(
        name="Coverage Suite",
        base_url="http://127.0.0.1:8000",
        tests=[
            TestCase(
                id="test_home_false_search_tag",
                name="Home smoke with search tag",
                start_url="http://127.0.0.1:8000/",
                tags=["search", "navigation"],
                actions=[
                    {"action": "click", "selector": "#nav-catalog"},
                ],
                expected=[
                    Expectation(type="url", description="Navigated", value="/catalog")
                ],
            ),
            TestCase(
                id="test_catalog_search_fail",
                name="Catalog search interaction",
                start_url="http://127.0.0.1:8000/catalog",
                tags=["search"],
                actions=[
                    {"action": "fill", "selector": "#catalog-search", "value": "laptop"},
                ],
                expected=[
                    Expectation(type="dom", description="Search box", selector="#catalog-search")
                ],
            ),
            TestCase(
                id="test_pricing_inconclusive",
                name="Pricing rule check",
                start_url="http://127.0.0.1:8000/pricing",
                tags=["pricing"],
                actions=[
                    {"action": "navigate", "url": "http://127.0.0.1:8000/pricing"},
                ],
                expected=[
                    Expectation(
                        type="business_rule",
                        description="Total price formula",
                        selector="#total",
                        inconclusive_if_missing_oracle=True,
                    )
                ],
            ),
        ],
    )

    # First check: if we only run test_home_false_search_tag on "/", feat_catalog_search on "/catalog" is NOT covered
    home_only_suite = TestSuite(
        name="Home Only",
        base_url="http://127.0.0.1:8000",
        tests=[suite.tests[0]],
    )
    analyzer = CoverageAnalyzer()
    home_cov = analyzer.analyze(registry, home_only_suite)
    assert "feat_home_nav" in home_cov.tested_feature_ids
    assert "feat_catalog_search" not in home_cov.tested_feature_ids

    # Second check: with full suite + execution report where test_home passes, test_catalog fails, and test_pricing is inconclusive
    now = datetime.now(UTC)
    run_report = TestRunReport.create(
        run_id="run-cov-1",
        suite_name="Coverage Suite",
        base_url="http://127.0.0.1:8000",
        started_at=now,
        finished_at=now,
        results=[
            TestResult(
                test_id="test_home_false_search_tag",
                status="pass",
                duration_seconds=0.04,
                jev_steps=[],
                verification_results=[],
                timestamp=now,
            ),
            TestResult(
                test_id="test_catalog_search_fail",
                status="fail",
                duration_seconds=0.04,
                jev_steps=[],
                verification_results=[],
                error_message="Search result mismatch",
                timestamp=now,
            ),
            TestResult(
                test_id="test_pricing_inconclusive",
                status="fail",
                duration_seconds=0.04,
                jev_steps=[],
                verification_results=[],
                inconclusive=True,
                inconclusive_reasons=["Missing explicit business-rule oracle"],
                timestamp=now,
            ),
        ],
    )
    verified_cov = analyzer.analyze(registry, suite, run_report=run_report)
    assert verified_cov.covered_features == 3
    assert verified_cov.verified_features == 1
    assert verified_cov.verified_feature_ids == ["feat_home_nav"]
    assert "feat_catalog_search" in verified_cov.failed_feature_ids
    assert "feat_pricing_calc" in verified_cov.inconclusive_feature_ids
    assert verified_cov.blocked_routes == {"http://127.0.0.1:8000/admin": "HTTP 403"}


def test_goal_decomposition_and_distinct_goal_driven_suites() -> None:
    """Different natural-language goals produce distinct test suites and explicit capability degradation notes."""
    inspected = InspectedPage(
        url="http://127.0.0.1:8000/",
        title="Enterprise Store",
        headings=["Enterprise Store"],
        search_inputs=[
            {"selector": "#search-box", "placeholder": "Search store", "name": "q"}
        ],
        buttons=[
            {"text": "Add to Cart", "selector": "#add-cart-btn"}
        ],
        nav_links=[
            {"text": "Catalog", "href": "http://127.0.0.1:8000/catalog", "selector": "#link-catalog"},
            {"text": "Checkout", "href": "http://127.0.0.1:8000/checkout", "selector": "#link-checkout"},
            {"text": "Admin Console", "href": "http://127.0.0.1:8000/admin", "selector": "#link-admin"},
        ],
    )
    registry = FeatureRegistry(
        base_url="http://127.0.0.1:8000",
        routes=[
            "http://127.0.0.1:8000",
            "http://127.0.0.1:8000/catalog",
            "http://127.0.0.1:8000/checkout",
            "http://127.0.0.1:8000/pricing",
        ],
        pages=[
            {"url": "http://127.0.0.1:8000", "title": "Enterprise Store"},
            {"url": "http://127.0.0.1:8000/catalog", "title": "Catalog"},
            {"url": "http://127.0.0.1:8000/checkout", "title": "Checkout"},
            {"url": "http://127.0.0.1:8000/pricing", "title": "Pricing"},
        ],
        features=[
            DiscoveredFeature(
                id="feat_admin",
                name="Admin Access Surface",
                type="admin",
                url="http://127.0.0.1:8000/admin",
                selector="#link-admin",
            ),
            DiscoveredFeature(
                id="feat_checkout",
                name="Checkout Flow",
                type="checkout",
                url="http://127.0.0.1:8000/checkout",
                selector="#link-checkout",
            ),
            DiscoveredFeature(
                id="feat_pricing",
                name="Coupon Discount Input",
                type="pricing",
                url="http://127.0.0.1:8000/pricing",
                selector="#coupon-input",
            ),
        ],
    )

    admin_intent = decompose_goal("Verify admin RBAC permissions")
    cart_intent = decompose_goal("Verify shopping cart and checkout")
    coupon_intent = decompose_goal("Verify coupon discount calculation")

    assert "admin" in admin_intent["flows"]
    assert "cart" in cart_intent["flows"] or "checkout" in cart_intent["flows"]
    assert "pricing" in coupon_intent["flows"]
    assert coupon_intent["requires_business_oracle"] is True

    planner = TestPlanner(api_key=None)
    suite_admin = planner.generate_suite(inspected, goal="Verify admin RBAC permissions", max_tests=4, registry=registry)
    suite_cart = planner.generate_suite(inspected, goal="Verify shopping cart and checkout", max_tests=4, registry=registry)
    suite_coupon = planner.generate_suite(inspected, goal="Verify coupon discount calculation", max_tests=4, registry=registry)

    admin_ids = [t.id for t in suite_admin.tests]
    cart_ids = [t.id for t in suite_cart.tests]
    coupon_ids = [t.id for t in suite_coupon.tests]

    assert admin_ids != cart_ids
    assert coupon_ids != admin_ids
    assert "ADMIN-RBAC-001" in admin_ids
    assert "CART-001" in cart_ids or "CHECKOUT-001" in cart_ids
    assert "RULE-DISCOUNT-001" in coupon_ids
    assert any("Capability degradation" in note for note in suite_coupon.planning_notes)

    # Verify scaling beyond 4 tests when max_tests=6
    suite_scaled = planner.generate_suite(inspected, goal=None, max_tests=6, registry=registry)
    assert len(suite_scaled.tests) > 4


def test_business_rule_oracle_verification_and_inconclusive_guard(
    multi_route_server: str,
    tmp_path: Path,
) -> None:
    """Business rules without an oracle are marked inconclusive; explicit oracle criteria verify DOM."""
    suite = TestSuite(
        name="Business Rule Oracle Suite",
        base_url=multi_route_server,
        tests=[
            TestCase(
                id="rule_without_oracle",
                name="Coupon rule without explicit oracle",
                start_url=f"{multi_route_server}/pricing",
                actions=[{"action": "navigate", "url": f"{multi_route_server}/pricing"}],
                expected=[
                    Expectation(
                        type="business_rule",
                        description="Verify coupon discount calculation without oracle",
                        oracle=None,
                        inconclusive_if_missing_oracle=True,
                    )
                ],
            ),
            TestCase(
                id="rule_with_valid_oracle",
                name="Coupon rule with explicit oracle ($85)",
                start_url=f"{multi_route_server}/pricing",
                actions=[{"action": "navigate", "url": f"{multi_route_server}/pricing"}],
                expected=[
                    Expectation(
                        type="business_rule",
                        description="Discounted total equals $85",
                        selector="#total",
                        oracle="$85",
                    )
                ],
            ),
        ],
    )
    runner = TestRunner(headless=True, screenshots_dir=tmp_path / "screenshots")
    report = asyncio.run(runner.run_suite(suite))
    by_id = {r.test_id: r for r in report.results}

    assert by_id["rule_without_oracle"].status == "fail"
    assert by_id["rule_without_oracle"].inconclusive is True
    assert by_id["rule_without_oracle"].failure_category == "inconclusive_business_rule"
    assert report.summary.inconclusive == 1

    assert by_id["rule_with_valid_oracle"].status == "pass"
    assert by_id["rule_with_valid_oracle"].inconclusive is False


def test_action_driver_reobservation_modal_dismissal_and_budgets() -> None:
    """ActionDriver records post-step state_delta, dismisses blocking overlays, and enforces step/cost budgets."""

    async def _exercise() -> None:
        async with async_playwright() as pw:
            browser = await pw.chromium.launch(headless=True)
            page = await browser.new_page()
            await page.set_content(
                """<!doctype html>
                <html>
                <head><title>Modal &amp; Budget Test</title></head>
                <body>
                  <button id="target-btn" onclick="document.getElementById('out').textContent='Clicked!'">Run Action</button>
                  <div id="out">Idle</div>
                  <div id="blocking-modal" role="dialog" aria-modal="true"
                       style="position:fixed;inset:0;background:rgba(0,0,0,0.8);z-index:9999;display:flex;align-items:center;justify-content:center;">
                    <button id="close-modal" aria-label="Close"
                            onclick="document.getElementById('blocking-modal').remove()">Close</button>
                  </div>
                </body>
                </html>"""
            )

            driver = ActionDriver(max_steps=1, max_cost_usd=0.05)
            outcomes = await driver.execute_goal(
                goal="Click target button",
                page=page,
                actions=[
                    {"action": "click", "selector": "#target-btn", "description": "Click target button"},
                    {"action": "wait", "timeout_ms": 100, "description": "Extra step exceeding budget"},
                ],
                max_steps=1,
            )
            assert len(outcomes) == 2
            assert outcomes[0]["status"] == "completed"
            assert outcomes[0]["observed_title"] == "Modal & Budget Test"
            assert outcomes[0]["state_delta"] is not None

            # Second step exceeds max_steps=1
            assert outcomes[1]["status"] == "failed"
            assert outcomes[1]["error"]["code"] == "budget_exceeded"

            await browser.close()

    asyncio.run(_exercise())


def test_site_crawler_multi_route_and_blocked_routes(multi_route_server: str) -> None:
    """SiteCrawler discovers multi-route features, page summaries, and records HTTP 403 blocked routes."""
    crawler = SiteCrawler(max_pages=5, headless=True)
    registry = asyncio.run(crawler.crawl(multi_route_server))

    assert any(r.endswith("/catalog") for r in registry.routes)
    assert any(r.endswith("/pricing") for r in registry.routes)
    assert any("/admin" in k and "403" in v for k, v in registry.blocked_routes.items())
    categories = {f.type for f in registry.features}
    assert "search" in categories
    assert "pricing" in categories
    assert len(registry.pages) >= 3


def test_closed_loop_auto_discovers_routes_and_writes_coverage(
    multi_route_server: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """_run_auto executes multi-page crawl, goal planning, execution, coverage analysis, and gap recovery."""
    monkeypatch.chdir(tmp_path)
    report = asyncio.run(
        _run_auto(
            url=multi_route_server,
            goal="Search catalog items",
            max_tests=4,
            max_pages=4,
            headless=True,
            output_dir=tmp_path,
        )
    )
    assert report is not None
    assert report.summary.total >= 2
    cov_files = list(tmp_path.glob("auto_coverage_*.json"))
    assert len(cov_files) == 1
    gap_files = list(tmp_path.glob("auto_gap_suite_*.json"))
    assert len(gap_files) == 1
    result_ids = [r.test_id for r in report.results]
    assert len(result_ids) == len(set(result_ids))


def test_benchmark_harness_report_timing_and_defect_evaluation(tmp_path: Path) -> None:
    """BenchmarkHarness measures report generation overhead and evaluates seeded & planner defect detection."""
    with BuggyFixtureSiteServer() as server:
        assert server.base_url.startswith("http://127.0.0.1:")

    harness = BenchmarkHarness(output_dir=tmp_path / "benchmarks", headless=True)
    defect_eval = asyncio.run(harness.evaluate_defect_detection(repeat_runs=1, workers=1))
    assert defect_eval["seeded_defects_total"] == 4
    assert defect_eval["defects_caught"] == 4
    assert defect_eval["defect_recall"] == 1.0
    assert defect_eval["healthy_controls_total"] == 2
    assert defect_eval["false_alarms"] == 0
    assert defect_eval["false_alarm_rate"] == 0.0
    assert defect_eval["precision"] == 1.0
    assert defect_eval["diagnosis_accuracy"] == 1.0
    assert defect_eval["inconclusive_rules_flagged"] == 1
    assert defect_eval["planner_defects_caught"] == 4
    assert defect_eval["planner_defect_recall"] == 1.0
    assert defect_eval["planner_false_alarms"] == 0


def test_precondition_token_boundary_and_cyclic_dag_ordering() -> None:
    """Precondition matching avoids substring collisions (test_1 vs test_10) and cyclic DAGs keep sinks last."""
    from aiqa.orchestrator.runner import _extract_test_dependencies

    runner = TestRunner(headless=True)
    test_1 = TestCase(
        id="test_1",
        name="Test 1",
        start_url="http://127.0.0.1:8000/",
        actions=[{"action": "navigate", "url": "http://127.0.0.1:8000/"}],
        expected=[Expectation(type="url", description="Home", value="/")],
    )
    test_10 = TestCase(
        id="test_10",
        name="Test 10",
        start_url="http://127.0.0.1:8000/",
        actions=[{"action": "navigate", "url": "http://127.0.0.1:8000/"}],
        expected=[Expectation(type="url", description="Home", value="/")],
    )
    test_child = TestCase(
        id="test_child",
        name="Depends only on test_10",
        start_url="http://127.0.0.1:8000/",
        preconditions=["Requires test_10 to pass"],
        actions=[{"action": "navigate", "url": "http://127.0.0.1:8000/"}],
        expected=[Expectation(type="url", description="Home", value="/")],
    )
    deps = _extract_test_dependencies(
        test_child, {"test_1", "test_10", "test_child"}
    )
    assert deps == ["test_10"]
    assert "test_1" not in deps
    assert test_1.id != test_10.id

    # Cyclic component A <-> B with sink C depending on B
    node_a = TestCase(
        id="node_a",
        name="Node A",
        start_url="http://127.0.0.1:8000/",
        depends_on=["node_b"],
        actions=[{"action": "navigate", "url": "http://127.0.0.1:8000/"}],
        expected=[Expectation(type="url", description="Home", value="/")],
    )
    node_b = TestCase(
        id="node_b",
        name="Node B",
        start_url="http://127.0.0.1:8000/",
        depends_on=["node_a"],
        actions=[{"action": "navigate", "url": "http://127.0.0.1:8000/"}],
        expected=[Expectation(type="url", description="Home", value="/")],
    )
    node_c = TestCase(
        id="node_c",
        name="Node C depends on B",
        start_url="http://127.0.0.1:8000/",
        depends_on=["node_b"],
        actions=[{"action": "navigate", "url": "http://127.0.0.1:8000/"}],
        expected=[Expectation(type="url", description="Home", value="/")],
    )
    groups = runner._group_tests_by_dependency([node_c, node_b, node_a])
    assert len(groups) == 1
    ordered_ids = [t.id for t in groups[0]]
    assert ordered_ids[-1] == "node_c"


def test_coverage_selector_less_tag_does_not_falsely_cover_element_feature() -> None:
    """A navigation test with a matching tag and destination URL must not falsely cover an element-specific feature."""
    registry = FeatureRegistry(
        base_url="http://127.0.0.1:8000",
        routes=["http://127.0.0.1:8000", "http://127.0.0.1:8000/catalog"],
        features=[
            DiscoveredFeature(
                id="feat_catalog_search",
                name="Catalog Search Input",
                type="search",
                url="http://127.0.0.1:8000/catalog",
                selector="#catalog-search",
            )
        ],
    )
    nav_only_suite = TestSuite(
        name="Nav Only With Search Tag",
        base_url="http://127.0.0.1:8000",
        tests=[
            TestCase(
                id="nav_to_catalog",
                name="Navigate to catalog page",
                start_url="http://127.0.0.1:8000/",
                tags=["search"],
                actions=[{"action": "click", "selector": "#nav-catalog"}],
                expected=[
                    Expectation(
                        type="url",
                        description="Arrived at catalog",
                        value="http://127.0.0.1:8000/catalog",
                    )
                ],
            )
        ],
    )
    cov = CoverageAnalyzer().analyze(registry, nav_only_suite)
    assert cov.covered_features == 0
    assert "feat_catalog_search" not in cov.tested_feature_ids


def test_spa_ecommerce_testid_and_title_contains_verification() -> None:
    """Verify 'be' substring title matching and SPA data-testid search/cart planning."""
    from aiqa.verifier.dom import DomVerifier

    verifier = DomVerifier()
    exp = Expectation(
        type="dom",
        description="Page title should contain 'Demo Store Testbed - Automated'",
        selector=None,
        value="Demo Store Testbed - Automated",
    )

    class _DummyPage:
        async def title(self) -> str:
            return "Demo Store Testbed - Automated QA Suite"

    res = asyncio.run(verifier.verify(exp, page=_DummyPage()))
    assert res.passed is True

    inspected = InspectedPage(
        url="http://127.0.0.1:8000/",
        title="Demo Store Testbed - Automated QA Suite",
        headings=["Demo Store"],
        search_inputs=[
            {
                "selector": "[data-testid='search-input']",
                "name": "q",
                "placeholder": "Search catalog items",
                "id": "",
                "testId": "search-input",
            }
        ],
        buttons=[
            {
                "text": "Search",
                "selector": "[data-testid='search-btn']",
                "tag": "button",
                "testId": "search-btn",
            },
            {
                "text": "Laptop",
                "selector": "[data-testid='hotword-Laptop']",
                "tag": "button",
                "testId": "hotword-Laptop",
            },
            {
                "text": "Add to Cart",
                "selector": "[data-testid='quick-add-cart-item_1']",
                "tag": "button",
                "testId": "quick-add-cart-item_1",
            },
        ],
        forms=[
            {
                "action": "",
                "method": "GET",
                "inputs": [
                    {
                        "type": "text",
                        "name": "q",
                        "id": "",
                        "placeholder": "Search catalog",
                        "selector": "[data-testid='search-input']",
                    }
                ],
            }
        ],
        nav_links=[
            {
                "text": "Sign In",
                "href": "http://127.0.0.1:8000/login",
                "testId": "nav-login-link",
            }
        ],
    )
    planner = TestPlanner(api_key=None)
    suite = planner.generate_suite(
        inspected,
        goal="Verify search, add to cart, and login flows",
        max_tests=5,
    )
    by_id = {t.id: t for t in suite.tests}
    assert "SEARCH-001" in by_id
    assert "CART-001" in by_id
    assert by_id["SEARCH-001"].actions[0].value == "Laptop"
    assert by_id["SEARCH-001"].actions[1].action == "click"
    assert by_id["CART-001"].start_url == "http://127.0.0.1:8000/"
    assert by_id["CART-001"].actions[0].selector == "[data-testid='quick-add-cart-item_1']"
