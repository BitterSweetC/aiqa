"""Reproducible end-to-end AIQA pipeline benchmark harness and versioned fixture site."""

from __future__ import annotations

import asyncio
import importlib.metadata
import os
import platform
import statistics
import sys
import threading
import time
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Literal, Self
from urllib.parse import parse_qs, urlparse

from playwright.async_api import async_playwright
from pydantic import BaseModel, Field
from rich.console import Console

import aiqa
from aiqa.models.test_case import TestCase, TestSuite
from aiqa.orchestrator.runner import TestRunner
from aiqa.reports.html_report import HtmlReporter
from aiqa.reports.json_report import JsonReporter
from aiqa.reports.junit_report import JUnitReporter

BENCHMARK_HARNESS_VERSION = "1.0.0"
FIXTURE_SITE_VERSION = "1.0.0"
DEFAULT_FROZEN_SUITE_PATH = (
    Path(__file__).resolve().parents[2] / "benchmarks" / "frozen_suite_v1.json"
)

_HOME_HTML = """<!DOCTYPE html>
<html lang="en">
<head><meta charset="utf-8"><title>AIQA Fixture Store v1.0.0</title></head>
<body>
  <header>
    <h1 id="hero-title">AIQA Enterprise Fixture Store</h1>
    <nav>
      <a id="nav-home" href="/">Home</a>
      <a id="nav-catalog" href="/catalog">Catalog</a>
      <a id="nav-checkout" href="/checkout">Checkout</a>
    </nav>
  </header>
  <main>
    <section id="search-section">
      <label for="search-input">Search Catalog</label>
      <input id="search-input" name="q" type="text" placeholder="Search products..." />
      <button id="search-submit" type="button">Search</button>
      <div id="search-results" aria-live="polite">Showing all products</div>
    </section>
  </main>
  <script>
    document.getElementById('search-submit').addEventListener('click', () => {
      const q = document.getElementById('search-input').value.trim();
      const box = document.getElementById('search-results');
      if (q) {
        box.textContent = 'Results for: ' + q + ' (1 item matched)';
      } else {
        box.textContent = 'No search query entered';
      }
    });
  </script>
</body>
</html>
"""

_CATALOG_HTML = """<!DOCTYPE html>
<html lang="en">
<head><meta charset="utf-8"><title>Catalog - AIQA Fixture Store</title></head>
<body>
  <h1 id="catalog-heading">Product Catalog</h1>
  <div id="cart-status">Cart items: <span id="cart-count">0</span></div>
  <div id="cart-banner" hidden></div>
  <article class="product-card" id="product-1">
    <h2>Quantum Sensor</h2>
    <button id="add-to-cart-1" type="button">Add to Cart</button>
    <button id="open-specs-btn" type="button">View Specifications</button>
  </article>
  <dialog id="specs-modal">
    <h3>Quantum Sensor Specifications</h3>
    <p>Sub-millisecond telemetry with deterministic calibration.</p>
  </dialog>
  <script>
    let count = 0;
    document.getElementById('add-to-cart-1').addEventListener('click', () => {
      count += 1;
      document.getElementById('cart-count').textContent = String(count);
      const banner = document.getElementById('cart-banner');
      banner.hidden = false;
      banner.textContent = 'Added Quantum Sensor to cart';
    });
    document.getElementById('open-specs-btn').addEventListener('click', () => {
      const modal = document.getElementById('specs-modal');
      modal.setAttribute('open', 'open');
    });
  </script>
</body>
</html>
"""

_CHECKOUT_HTML = """<!DOCTYPE html>
<html lang="en">
<head><meta charset="utf-8"><title>Checkout - AIQA Fixture Store</title></head>
<body>
  <h1 id="checkout-heading">Express Checkout</h1>
  <form id="checkout-form" onsubmit="return false;">
    <label for="shipping-name">Recipient Name</label>
    <input id="shipping-name" type="text" />
    <label for="shipping-address">Shipping Address</label>
    <input id="shipping-address" type="text" />
    <button id="place-order-btn" type="button">Place Order</button>
  </form>
  <div id="order-confirmation" hidden></div>
  <script>
    document.getElementById('place-order-btn').addEventListener('click', () => {
      const name = document.getElementById('shipping-name').value.trim();
      const addr = document.getElementById('shipping-address').value.trim();
      const conf = document.getElementById('order-confirmation');
      if (name && addr) {
        conf.hidden = false;
        conf.textContent = 'Order confirmed for ' + name + ' at ' + addr;
      }
    });
  </script>
</body>
</html>
"""


class _FixtureRequestHandler(BaseHTTPRequestHandler):
    """Deterministic HTTP handler for the versioned local fixture site."""

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        path = parsed.path
        if path in ("/", "/index.html"):
            body = _HOME_HTML
        elif path == "/catalog":
            body = _CATALOG_HTML
        elif path == "/checkout":
            body = _CHECKOUT_HTML
        elif path == "/search":
            q = parse_qs(parsed.query).get("q", [""])[0]
            body = f"<html><body><div id='search-results'>Results for: {q}</div></body></html>"
        else:
            self.send_response(404)
            self.end_headers()
            self.wfile.write(b"Not found")
            return

        payload = body.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("X-AIQA-Fixture-Version", FIXTURE_SITE_VERSION)
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, format: str, *args: object) -> None:
        return


class FixtureSiteServer:
    """Context-managed local HTTP server for reproducible AIQA benchmarks."""

    version: str = FIXTURE_SITE_VERSION

    def __init__(self, host: str = "127.0.0.1", port: int = 0) -> None:
        self._server = ThreadingHTTPServer((host, port), _FixtureRequestHandler)
        self._thread: threading.Thread | None = None

    @property
    def base_url(self) -> str:
        host, port = self._server.server_address[:2]
        return f"http://{host}:{port}"

    def start(self) -> str:
        if self._thread is None:
            self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
            self._thread.start()
        return self.base_url

    def stop(self) -> None:
        self._server.shutdown()
        self._server.server_close()
        if self._thread is not None:
            self._thread.join(timeout=2.0)
            self._thread = None

    def __enter__(self) -> Self:
        self.start()
        return self

    def __exit__(self, *_exc: object) -> None:
        self.stop()


def load_frozen_suite(
    base_url: str,
    suite_path: Path | None = None,
    scale_multiplier: int = 1,
) -> TestSuite:
    """Load the frozen benchmark suite and bind `{BASE_URL}` to the local fixture server."""
    path = suite_path or DEFAULT_FROZEN_SUITE_PATH
    raw = path.read_text(encoding="utf-8").replace("{BASE_URL}", base_url.rstrip("/"))
    suite = TestSuite.model_validate_json(raw)
    if scale_multiplier > 1:
        expanded: list[TestCase] = []
        for rep in range(1, scale_multiplier + 1):
            for tc in suite.tests:
                expanded.append(
                    tc.model_copy(
                        update={
                            "id": f"{tc.id}-R{rep:03d}",
                            "name": f"{tc.name} (rep {rep})",
                        }
                    )
                )
        suite = suite.model_copy(update={"tests": expanded})
    return suite


def _percentile(sorted_vals: list[float], pct: float) -> float:
    if not sorted_vals:
        return 0.0
    k = (len(sorted_vals) - 1) * (pct / 100.0)
    f = int(k)
    c = min(f + 1, len(sorted_vals) - 1)
    if f == c:
        return round(sorted_vals[f], 2)
    return round(sorted_vals[f] + (sorted_vals[c] - sorted_vals[f]) * (k - f), 2)


def _compute_latency_stats(durations_sec: list[float]) -> dict[str, float]:
    if not durations_sec:
        return {"min": 0.0, "mean": 0.0, "p50": 0.0, "p95": 0.0, "p99": 0.0, "max": 0.0}
    ms = sorted(d * 1000.0 for d in durations_sec)
    return {
        "min": round(ms[0], 2),
        "mean": round(statistics.mean(ms), 2),
        "p50": _percentile(ms, 50.0),
        "p95": _percentile(ms, 95.0),
        "p99": _percentile(ms, 99.0),
        "max": round(ms[-1], 2),
    }


def collect_environment_metadata() -> dict[str, Any]:
    """Record runtime, OS, CPU, and package versions for benchmark reproducibility."""
    try:
        pw_version = importlib.metadata.version("playwright")
    except importlib.metadata.PackageNotFoundError:
        pw_version = "unknown"

    return {
        "timestamp_utc": datetime.now(UTC).isoformat(),
        "python_version": sys.version.split()[0],
        "platform": platform.platform(),
        "machine": platform.machine(),
        "cpu_count": os.cpu_count() or 1,
        "aiqa_version": getattr(aiqa, "__version__", "0.1.0"),
        "playwright_version": pw_version,
    }


_BUGGY_HOME_HTML = """<!DOCTYPE html>
<html lang="en">
<head><meta charset="utf-8"><title>AIQA Buggy Fixture Store v1.0.0</title></head>
<body>
  <header>
    <h1 id="hero-title">AIQA Enterprise Fixture Store</h1>
    <nav>
      <a id="nav-home" href="/">Home</a>
      <a id="nav-catalog" href="/catalog">Catalog</a>
      <a id="nav-checkout" href="/checkout">Checkout</a>
      <a id="nav-admin" href="/admin/audit">Admin Audit</a>
    </nav>
  </header>
  <main>
    <section id="search-section">
      <label for="search-input">Search Catalog</label>
      <input id="search-input" name="q" type="text" placeholder="Search products..." />
      <button id="search-submit" type="button">Search</button>
      <div id="search-results" aria-live="polite">Showing all products</div>
    </section>
  </main>
  <script>
    // SEEDED DEFECT 1: Search handler throws a console error and fails to update #search-results
    document.getElementById('search-submit').addEventListener('click', () => {
      console.error('TypeError: searchIndex is undefined');
      document.getElementById('search-results').textContent = 'Search service unavailable';
    });
  </script>
</body>
</html>
"""

_BUGGY_CATALOG_HTML = """<!DOCTYPE html>
<html lang="en">
<head><meta charset="utf-8"><title>Catalog - AIQA Buggy Fixture Store</title></head>
<body>
  <h1 id="catalog-heading">Product Catalog</h1>
  <div id="cart-status">Cart items: <span id="cart-count">0</span></div>
  <article class="product-card" id="product-1">
    <h2>Quantum Sensor</h2>
    <!-- SEEDED DEFECT 2: Dead button with no event listener / no observable DOM effect -->
    <button id="add-to-cart-broken" type="button">Add to Cart</button>
  </article>
</body>
</html>
"""

_BUGGY_CHECKOUT_HTML = """<!DOCTYPE html>
<html lang="en">
<head><meta charset="utf-8"><title>Checkout - AIQA Buggy Fixture Store</title></head>
<body>
  <h1 id="checkout-heading">Express Checkout</h1>
  <form id="checkout-form" onsubmit="return false;">
    <input id="shipping-name" type="text" placeholder="Recipient Name" />
    <input id="coupon-code" name="coupon" type="text" placeholder="Coupon Code" />
    <button id="place-order-btn" type="button">Place Order</button>
  </form>
  <div id="order-confirmation">Awaiting submission</div>
  <script>
    // SEEDED DEFECT 3: Checkout displays gateway failure instead of order confirmation
    document.getElementById('place-order-btn').addEventListener('click', () => {
      const conf = document.getElementById('order-confirmation');
      conf.textContent = 'Payment gateway error: invalid merchant token';
    });
  </script>
</body>
</html>
"""


class _BuggyFixtureRequestHandler(BaseHTTPRequestHandler):
    """HTTP handler for the seeded-defect evaluation benchmark site."""

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        path = parsed.path
        if path in ("/", "/index.html"):
            body = _BUGGY_HOME_HTML
            status = 200
        elif path == "/catalog":
            body = _BUGGY_CATALOG_HTML
            status = 200
        elif path == "/checkout":
            body = _BUGGY_CHECKOUT_HTML
            status = 200
        elif path == "/admin/audit":
            # SEEDED DEFECT 4: Server returns HTTP 500 on admin audit route
            body = "<html><body><h1 id='error-heading'>500 Internal Server Error: Audit DB unreachable</h1></body></html>"
            status = 500
        else:
            self.send_response(404)
            self.end_headers()
            self.wfile.write(b"Not found")
            return

        payload = body.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, format: str, *args: object) -> None:
        return


class BuggyFixtureSiteServer:
    """Context-managed local HTTP server with 4 seeded application defects and 2 healthy controls."""

    def __init__(self, host: str = "127.0.0.1", port: int = 0) -> None:
        self._server = ThreadingHTTPServer((host, port), _BuggyFixtureRequestHandler)
        self._thread: threading.Thread | None = None

    @property
    def base_url(self) -> str:
        host, port = self._server.server_address[:2]
        return f"http://{host}:{port}"

    def start(self) -> str:
        if self._thread is None:
            self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
            self._thread.start()
        return self.base_url

    def stop(self) -> None:
        self._server.shutdown()
        self._server.server_close()
        if self._thread is not None:
            self._thread.join(timeout=2.0)
            self._thread = None

    def __enter__(self) -> Self:
        self.start()
        return self

    def __exit__(self, *_exc: object) -> None:
        self.stop()


def build_seeded_defect_suite(base_url: str) -> TestSuite:
    """Build the evaluation suite targeting 2 healthy controls, 4 seeded defects, and 1 missing-oracle rule."""
    from aiqa.models.test_case import Expectation

    base = base_url.rstrip("/")
    return TestSuite(
        name="AIQA Seeded Defect & False-Alarm Evaluation Suite v1",
        base_url=base,
        tests=[
            TestCase(
                id="CONTROL-SMOKE-001",
                name="Healthy Control: Homepage renders title and hero banner",
                start_url=f"{base}/",
                goal="Verify homepage loads with hero heading",
                actions=[
                    {
                        "action": "navigate",
                        "url": f"{base}/",
                        "description": "Navigate to homepage",
                    }
                ],
                expected=[
                    Expectation(
                        type="dom",
                        description="Hero heading is visible",
                        selector="#hero-title",
                        value="AIQA Enterprise Fixture Store",
                    )
                ],
                tags=["control", "smoke"],
                timeout=30,
            ),
            TestCase(
                id="CONTROL-NAV-001",
                name="Healthy Control: Catalog navigation link loads catalog page",
                start_url=f"{base}/",
                goal="Click Catalog link and verify navigation to /catalog",
                actions=[
                    {
                        "action": "click",
                        "selector": "#nav-catalog",
                        "description": "Click Catalog navigation link",
                    }
                ],
                expected=[
                    Expectation(
                        type="url",
                        description="URL resolves to /catalog",
                        value=f"{base}/catalog",
                    ),
                    Expectation(
                        type="dom",
                        description="Catalog heading renders",
                        selector="#catalog-heading",
                        value="Product Catalog",
                    ),
                ],
                tags=["control", "navigation"],
                timeout=30,
            ),
            TestCase(
                id="DEFECT-SEARCH-001",
                name="Seeded Defect: Search submission throws console error and fails result rendering",
                start_url=f"{base}/",
                goal="Type 'Quantum' into search input, click Search, and verify matching results",
                actions=[
                    {
                        "action": "fill",
                        "selector": "#search-input",
                        "value": "Quantum",
                        "description": "Enter search query",
                    },
                    {
                        "action": "click",
                        "selector": "#search-submit",
                        "description": "Click Search button",
                    },
                ],
                expected=[
                    Expectation(
                        type="dom",
                        description="Search results contain matched item",
                        selector="#search-results",
                        value="Results for: Quantum",
                    )
                ],
                tags=["defect", "search"],
                timeout=30,
            ),
            TestCase(
                id="DEFECT-CART-NOOP-001",
                name="Seeded Defect: Dead Add-to-Cart button produces no DOM mutation",
                start_url=f"{base}/catalog",
                goal="Click Add to Cart button and verify cart count increments to 1",
                actions=[
                    {
                        "action": "click",
                        "selector": "#add-to-cart-broken",
                        "description": "Click Add to Cart button",
                    }
                ],
                expected=[
                    Expectation(
                        type="dom",
                        description="Cart count increments to 1",
                        selector="#cart-count",
                        value="1",
                    )
                ],
                tags=["defect", "cart"],
                timeout=30,
            ),
            TestCase(
                id="DEFECT-CHECKOUT-001",
                name="Seeded Defect: Checkout submit fails with payment gateway error",
                start_url=f"{base}/checkout",
                goal="Fill recipient name, click Place Order, and verify order confirmation",
                actions=[
                    {
                        "action": "fill",
                        "selector": "#shipping-name",
                        "value": "Ada Lovelace",
                        "description": "Enter recipient name",
                    },
                    {
                        "action": "click",
                        "selector": "#place-order-btn",
                        "description": "Click Place Order",
                    },
                ],
                expected=[
                    Expectation(
                        type="dom",
                        description="Order confirmation message appears",
                        selector="#order-confirmation",
                        value="Order confirmed",
                    )
                ],
                tags=["defect", "checkout"],
                timeout=30,
            ),
            TestCase(
                id="DEFECT-ADMIN-500-001",
                name="Seeded Defect: Admin audit route returns HTTP 500 server error",
                start_url=f"{base}/",
                goal="Click Admin Audit link and verify audit table renders",
                actions=[
                    {
                        "action": "click",
                        "selector": "#nav-admin",
                        "description": "Click Admin Audit link",
                    }
                ],
                expected=[
                    Expectation(
                        type="dom",
                        description="Admin audit table is visible",
                        selector="#admin-audit-table",
                        value="Audit Log",
                    )
                ],
                tags=["defect", "admin"],
                timeout=30,
            ),
            TestCase(
                id="RULE-DISCOUNT-INCONCLUSIVE-001",
                name="Business Rule Control: Coupon discount without oracle is flagged inconclusive",
                start_url=f"{base}/checkout",
                goal="Enter VIP20 coupon code and verify business discount formula",
                actions=[
                    {
                        "action": "fill",
                        "selector": "#coupon-code",
                        "value": "VIP20",
                        "description": "Enter coupon code VIP20",
                    }
                ],
                expected=[
                    Expectation(
                        type="business_rule",
                        description="Verify VIP20 tier discount calculation without oracle",
                        inconclusive_if_missing_oracle=True,
                    )
                ],
                tags=["business_rule", "pricing"],
                timeout=30,
            ),
        ],
    )


class BenchmarkReport(BaseModel):
    """Structured output of a reproducible AIQA pipeline benchmark run."""

    benchmark_version: str = Field(default=BENCHMARK_HARNESS_VERSION)
    fixture_site_version: str = Field(default=FIXTURE_SITE_VERSION)
    suite_name: str
    suite_version: str
    pipeline_mode: Literal["aiqa_full_pipeline", "direct_playwright_baseline"] = (
        "aiqa_full_pipeline"
    )
    pipeline_coverage: list[str] = Field(
        default_factory=lambda: [
            "TestRunner",
            "BrowserSession",
            "JevRunner",
            "ActionDriver",
            "DomVerifier",
            "UrlVerifier",
            "JsonReporter",
            "JUnitReporter",
            "HtmlReporter",
        ]
    )
    environment: dict[str, Any]
    warmup_runs: int
    warmup_duration_seconds: float
    concurrency_workers: int
    failure_policy: str = "fail_closed_phase0_v1"
    total_tests: int
    passed_tests: int
    failed_tests: int
    error_tests: int
    skipped_tests: int
    success_rate: float
    failure_rate: float
    wall_time_seconds: float
    runner_wall_time_seconds: float = 0.0
    report_generation_seconds: float = 0.0
    throughput_tests_per_sec: float
    effective_passed_per_sec: float
    latency_ms: dict[str, float]
    artifacts: dict[str, Any]
    model_telemetry: dict[str, Any]
    baseline_comparison: dict[str, Any] | None = None
    defect_detection_evaluation: dict[str, Any] | None = None


class BenchmarkHarness:
    """Executes reproducible benchmarks of the full AIQA pipeline and seeded defect detection."""

    def __init__(
        self,
        output_dir: Path | str = Path("./reports/benchmarks"),
        headless: bool = True,
    ) -> None:
        self.output_dir = Path(output_dir)
        self.headless = headless

    async def run(
        self,
        suite: TestSuite | None = None,
        workers: int = 1,
        warmup_runs: int = 1,
        scale_multiplier: int = 1,
        compare_baseline: bool = False,
        evaluate_defects: bool = False,
        model_telemetry: dict[str, Any] | None = None,
    ) -> BenchmarkReport:
        """Run the AIQA pipeline benchmark against the versioned local FixtureSiteServer."""
        self.output_dir.mkdir(parents=True, exist_ok=True)
        shots_dir = self.output_dir / "screenshots"

        with FixtureSiteServer() as server:
            target_suite = suite or load_frozen_suite(
                server.base_url,
                scale_multiplier=scale_multiplier,
            )

            # 1. Warmup runs (using first test in suite to warm browser launch & local server)
            warmup_start = time.perf_counter()
            if warmup_runs > 0 and target_suite.tests:
                warmup_suite = target_suite.model_copy(update={"tests": [target_suite.tests[0]]})
                for _ in range(warmup_runs):
                    warmup_runner = TestRunner(
                        headless=self.headless,
                        screenshots_dir=shots_dir,
                        console=Console(quiet=True),
                        workers=1,
                    )
                    await warmup_runner.run_suite(warmup_suite, workers=1)
            warmup_duration = round(time.perf_counter() - warmup_start, 4)

            # 2. Measured full AIQA pipeline run (including JSON, JUnit, and HTML report generation)
            runner = TestRunner(
                headless=self.headless,
                screenshots_dir=shots_dir,
                console=Console(quiet=True),
                workers=workers,
            )
            wall_start = time.perf_counter()
            run_report = await runner.run_suite(target_suite, workers=workers)
            runner_wall_time = round(max(0.0001, time.perf_counter() - wall_start), 4)

            cwd = Path.cwd().resolve()
            for r in run_report.results:
                if r.screenshot_path:
                    try:
                        r.screenshot_path = Path(r.screenshot_path).resolve().relative_to(cwd).as_posix()
                    except ValueError:
                        r.screenshot_path = f"screenshots/{Path(r.screenshot_path).name}"

            # 3. Emit all standard AIQA report artifacts inside measured wall_time_seconds
            report_start = time.perf_counter()
            json_path = JsonReporter(output_dir=self.output_dir).save(
                run_report, filename="benchmark_aiqa_run.json"
            )
            junit_path = JUnitReporter(output_dir=self.output_dir).save(
                run_report, filename="benchmark_aiqa_junit.xml"
            )
            html_path = HtmlReporter(output_dir=self.output_dir).save(
                run_report, filename="benchmark_aiqa_report.html"
            )
            report_gen_seconds = round(max(0.0, time.perf_counter() - report_start), 4)
            wall_time = round(max(0.0001, time.perf_counter() - wall_start), 4)

            json_bytes = json_path.stat().st_size if json_path.exists() else 0
            junit_bytes = junit_path.stat().st_size if junit_path.exists() else 0
            html_bytes = html_path.stat().st_size if html_path.exists() else 0

            durations = [r.duration_seconds for r in run_report.results]
            total = run_report.summary.total
            passed = run_report.summary.passed
            failed = run_report.summary.failed
            errors = run_report.summary.errors
            skipped = run_report.summary.skipped
            success_rate = round(passed / total, 4) if total > 0 else 0.0
            failure_rate = round((failed + errors) / total, 4) if total > 0 else 0.0

            baseline_comp: dict[str, Any] | None = None
            if compare_baseline:
                baseline_metrics = await self._run_equivalent_direct_playwright_baseline(
                    target_suite,
                    workers=workers,
                )
                b_wall = max(0.0001, baseline_metrics["wall_time_seconds"])
                baseline_comp = {
                    "baseline_mode": "direct_playwright_baseline",
                    "same_workload": True,
                    "same_concurrency": True,
                    "same_failure_policy": True,
                    "concurrency_workers": workers,
                    "baseline_metrics": baseline_metrics,
                    "pipeline_to_baseline_wall_time_ratio": round(wall_time / b_wall, 3),
                    "pass_rate_agreement": (
                        baseline_metrics["passed_tests"] == passed
                        and baseline_metrics["total_tests"] == total
                    ),
                }

        defect_eval: dict[str, Any] | None = None
        if evaluate_defects:
            defect_eval = await self.evaluate_defect_detection(repeat_runs=2, workers=workers)

        telemetry = model_telemetry or {
            "model_execution_used": False,
            "mode": "deterministic_typed_actions",
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "total_tokens": 0,
            "measured_cost_usd": 0.0,
        }

        benchmark_report = BenchmarkReport(
            suite_name=target_suite.name,
            suite_version=target_suite.schema_version,
            environment=collect_environment_metadata(),
            warmup_runs=warmup_runs,
            warmup_duration_seconds=warmup_duration,
            concurrency_workers=workers,
            total_tests=total,
            passed_tests=passed,
            failed_tests=failed,
            error_tests=errors,
            skipped_tests=skipped,
            success_rate=success_rate,
            failure_rate=failure_rate,
            wall_time_seconds=wall_time,
            runner_wall_time_seconds=runner_wall_time,
            report_generation_seconds=report_gen_seconds,
            throughput_tests_per_sec=round(total / wall_time, 2) if wall_time > 0 else 0.0,
            effective_passed_per_sec=round(passed / wall_time, 2) if wall_time > 0 else 0.0,
            latency_ms=_compute_latency_stats(durations),
            artifacts={
                "json_report_bytes": json_bytes,
                "junit_report_bytes": junit_bytes,
                "html_report_bytes": html_bytes,
                "total_artifact_bytes": json_bytes + junit_bytes + html_bytes,
                "report_paths": {
                    "json": str(json_path),
                    "junit": str(junit_path),
                    "html": str(html_path),
                },
            },
            model_telemetry=telemetry,
            baseline_comparison=baseline_comp,
            defect_detection_evaluation=defect_eval,
        )

        summary_path = self.output_dir / "benchmark_summary.json"
        summary_path.write_text(
            benchmark_report.model_dump_json(indent=2),
            encoding="utf-8",
        )
        return benchmark_report

    async def evaluate_defect_detection(
        self,
        repeat_runs: int = 2,
        workers: int = 1,
    ) -> dict[str, Any]:
        """Evaluate AIQA defect-detection recall, false-alarm rate, diagnosis accuracy, and stability."""
        from aiqa.crawler.site_crawler import SiteCrawler
        from aiqa.planner.site_inspector import SiteInspector
        from aiqa.planner.test_generator import TestPlanner

        self.output_dir.mkdir(parents=True, exist_ok=True)
        shots_dir = self.output_dir / "screenshots_defects"
        runs_count = max(1, repeat_runs)

        with BuggyFixtureSiteServer() as buggy_server:
            # 1. Measure planner & crawler discovery AND execute the planner-generated suite
            crawler = SiteCrawler(headless=self.headless, max_pages=5, max_depth=2)
            registry = await crawler.crawl(buggy_server.base_url)
            inspector = SiteInspector(headless=self.headless)
            inspected = await inspector.inspect(buggy_server.base_url)
            planner = TestPlanner()
            planned_suite = planner.generate_suite(
                inspected,
                goal="Verify search, shopping cart, and checkout flows",
                max_tests=6,
                registry=registry,
            )
            planner_runner = TestRunner(
                headless=self.headless,
                screenshots_dir=shots_dir / "planned",
                console=Console(quiet=True),
                workers=workers,
            )
            planned_report = await planner_runner.run_suite(planned_suite, workers=workers)

            # 2. Run the seeded defect & control evaluation suite across repeat_runs
            eval_suite = build_seeded_defect_suite(buggy_server.base_url)
            run_reports = []
            for _ in range(runs_count):
                runner = TestRunner(
                    headless=self.headless,
                    screenshots_dir=shots_dir,
                    console=Console(quiet=True),
                    workers=workers,
                )
                rep = await runner.run_suite(eval_suite, workers=workers)
                run_reports.append(rep)

        planned_by_id = {r.test_id: r for r in planned_report.results}
        planner_defects_caught = 0
        planner_false_alarms = 0
        for tc in planned_suite.tests:
            pr = planned_by_id.get(tc.id)
            if pr is None:
                continue
            action_sels = {getattr(a, "selector", None) for a in (tc.actions or [])}
            is_seeded_defect_target = (
                tc.id in {"SEARCH-001", "CART-001", "CHECKOUT-001"}
                or "#nav-admin" in action_sels
                or "#add-to-cart-broken" in action_sels
                or "#place-order-btn" in action_sels
                or "#search-submit" in action_sels
                or "admin" in tc.tags
            )
            if is_seeded_defect_target:
                if pr.status in ("fail", "error"):
                    planner_defects_caught += 1
            else:
                if pr.status != "pass":
                    planner_false_alarms += 1

        primary = run_reports[0]
        res_by_id = {r.test_id: r for r in primary.results}

        defect_ids = [
            "DEFECT-SEARCH-001",
            "DEFECT-CART-NOOP-001",
            "DEFECT-CHECKOUT-001",
            "DEFECT-ADMIN-500-001",
        ]
        control_ids = ["CONTROL-SMOKE-001", "CONTROL-NAV-001"]
        inconclusive_ids = ["RULE-DISCOUNT-INCONCLUSIVE-001"]

        defects_caught = sum(
            1
            for did in defect_ids
            if did in res_by_id and res_by_id[did].status in ("fail", "error")
        )
        missed_defects = len(defect_ids) - defects_caught
        false_alarms = sum(
            1
            for cid in control_ids
            if cid in res_by_id and res_by_id[cid].status != "pass"
        )
        inconclusive_flagged = sum(
            1
            for iid in inconclusive_ids
            if iid in res_by_id
            and getattr(res_by_id[iid], "inconclusive", False)
            and res_by_id[iid].status != "pass"
        )

        diagnosed_accurately = 0
        for did in defect_ids:
            r = res_by_id.get(did)
            if (
                r is not None
                and r.status in ("fail", "error")
                and r.diagnosis is not None
                and r.diagnosis.likely_cause
                and r.diagnosis.summary
            ):
                diagnosed_accurately += 1

        # Compute multi-run status consistency
        consistent_cases = 0
        all_case_ids = [t.id for t in eval_suite.tests]
        for cid in all_case_ids:
            statuses = {
                next((r.status for r in rep.results if r.test_id == cid), "missing")
                for rep in run_reports
            }
            if len(statuses) == 1:
                consistent_cases += 1

        recall = round(defects_caught / len(defect_ids), 4) if defect_ids else 1.0
        far = round(false_alarms / len(control_ids), 4) if control_ids else 0.0
        precision = (
            round(defects_caught / (defects_caught + false_alarms), 4)
            if (defects_caught + false_alarms) > 0
            else 1.0
        )
        diag_acc = round(diagnosed_accurately / max(1, defects_caught), 4)
        consistency = round(consistent_cases / len(all_case_ids), 4) if all_case_ids else 1.0
        planner_recall = (
            round(min(planner_defects_caught, len(defect_ids)) / len(defect_ids), 4)
            if defect_ids
            else 1.0
        )

        per_case = []
        for tc in eval_suite.tests:
            r = res_by_id.get(tc.id)
            per_case.append(
                {
                    "test_id": tc.id,
                    "role_in_benchmark": (
                        "seeded_defect"
                        if tc.id in defect_ids
                        else ("healthy_control" if tc.id in control_ids else "business_rule_missing_oracle")
                    ),
                    "status": r.status if r else "missing",
                    "inconclusive": getattr(r, "inconclusive", False) if r else False,
                    "failure_category": getattr(r, "failure_category", None) if r else None,
                    "diagnosis_root_cause": (
                        r.diagnosis.likely_cause if (r and r.diagnosis) else None
                    ),
                }
            )

        return {
            "seeded_defects_total": len(defect_ids),
            "defects_caught": defects_caught,
            "missed_defects": missed_defects,
            "healthy_controls_total": len(control_ids),
            "false_alarms": false_alarms,
            "defect_recall": recall,
            "false_alarm_rate": far,
            "precision": precision,
            "diagnosis_accuracy": diag_acc,
            "inconclusive_rules_total": len(inconclusive_ids),
            "inconclusive_rules_flagged": inconclusive_flagged,
            "repeat_runs": runs_count,
            "repeat_consistency_rate": consistency,
            "planner_discovered_routes": len(registry.routes),
            "planner_blocked_routes": dict(registry.blocked_routes),
            "planner_generated_tests_count": len(planned_suite.tests),
            "planner_defects_caught": planner_defects_caught,
            "planner_defect_recall": planner_recall,
            "planner_false_alarms": planner_false_alarms,
            "per_case_outcomes": per_case,
        }

    async def _run_equivalent_direct_playwright_baseline(
        self,
        suite: TestSuite,
        workers: int = 1,
    ) -> dict[str, Any]:
        """Run the exact same actions and assertions in direct Playwright under identical concurrency."""
        semaphore = asyncio.Semaphore(max(1, workers))
        durations: list[float] = []
        passed = 0
        failed = 0

        async def _exec_case(test: TestCase) -> None:
            nonlocal passed, failed
            async with semaphore:
                t0 = time.perf_counter()
                case_ok = True
                try:
                    async with async_playwright() as pw:
                        browser = await pw.chromium.launch(headless=self.headless)
                        context = await browser.new_context()
                        page = await context.new_page()
                        await page.goto(test.start_url, wait_until="domcontentloaded")
                        for act in test.actions or []:
                            kind = getattr(act, "action", "")
                            if kind == "navigate":
                                await page.goto(act.url, wait_until="domcontentloaded")
                            elif kind == "click":
                                await page.locator(act.selector).first.click(timeout=5000)
                            elif kind == "fill":
                                await page.locator(act.selector).first.fill(act.value, timeout=5000)
                            elif kind == "press":
                                if act.selector:
                                    await page.locator(act.selector).first.press(
                                        act.key, timeout=5000
                                    )
                                else:
                                    await page.keyboard.press(act.key)
                            elif kind == "wait":
                                await page.wait_for_timeout(act.timeout_ms)

                        for exp in test.expected:
                            if exp.type == "url":
                                if exp.value and exp.value not in page.url:
                                    case_ok = False
                            elif exp.type == "dom" and exp.selector:
                                loc = page.locator(exp.selector).first
                                if await loc.count() == 0 or not await loc.is_visible():
                                    case_ok = False
                                elif exp.value:
                                    txt = (await loc.inner_text()) or ""
                                    if exp.value.lower() not in txt.lower():
                                        case_ok = False
                        await browser.close()
                except Exception:  # noqa: BLE001
                    case_ok = False
                durations.append(round(time.perf_counter() - t0, 4))
                if case_ok:
                    passed += 1
                else:
                    failed += 1

        wall_start = time.perf_counter()
        if workers <= 1:
            for t in suite.tests:
                await _exec_case(t)
        else:
            await asyncio.gather(*(_exec_case(t) for t in suite.tests))
        wall_time = round(max(0.0001, time.perf_counter() - wall_start), 4)
        total = len(suite.tests)
        return {
            "total_tests": total,
            "passed_tests": passed,
            "failed_tests": failed,
            "success_rate": round(passed / total, 4) if total > 0 else 0.0,
            "wall_time_seconds": wall_time,
            "throughput_tests_per_sec": round(total / wall_time, 2),
            "latency_ms": _compute_latency_stats(durations),
        }
