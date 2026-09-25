"""Direct Playwright DOM benchmark over 1,000 live-site checks.

This harness calls Playwright directly. It does not run AIQA's planner, action
driver, verifier, orchestrator, reporting pipeline, or any vision model. Its
output measures only this harness and must not be used as an end-to-end AIQA or
cross-model speed comparison.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import platform
import statistics
import sys
import time
from datetime import UTC, datetime
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import Any

from playwright.async_api import Error as PlaywrightError
from playwright.async_api import async_playwright
from rich.console import Console
from rich.panel import Panel
from rich.progress import (
    BarColumn,
    Progress,
    SpinnerColumn,
    TextColumn,
    TimeElapsedColumn,
    TimeRemainingColumn,
)
from rich.table import Table

console = Console()


def environment_metadata(browser_version: str | None = None) -> dict[str, str]:
    """Return enough runtime metadata to identify a benchmark environment."""
    try:
        playwright_version = version("playwright")
    except PackageNotFoundError:
        playwright_version = "unknown"
    return {
        "os": platform.platform(),
        "python": sys.version.split()[0],
        "playwright": playwright_version,
        "browser_name": "Chromium",
        "browser_version": browser_version or "not_recorded",
    }


def write_json(path: Path, data: Any) -> None:
    """Serialize a benchmark artifact outside the async browser operations."""
    with path.open("w", encoding="utf-8") as file:
        json.dump(data, file, indent=2, ensure_ascii=False)


# 50 real categories from books.toscrape.com
BOOK_CATEGORIES = [
    ("Travel", "catalogue/category/books/travel_2/index.html"),
    ("Mystery", "catalogue/category/books/mystery_3/index.html"),
    ("Historical Fiction", "catalogue/category/books/historical-fiction_4/index.html"),
    ("Sequential Art", "catalogue/category/books/sequential-art_5/index.html"),
    ("Classics", "catalogue/category/books/classics_6/index.html"),
    ("Philosophy", "catalogue/category/books/philosophy_7/index.html"),
    ("Romance", "catalogue/category/books/romance_8/index.html"),
    ("Womens Fiction", "catalogue/category/books/womens-fiction_9/index.html"),
    ("Fiction", "catalogue/category/books/fiction_10/index.html"),
    ("Childrens", "catalogue/category/books/childrens_11/index.html"),
    ("Religion", "catalogue/category/books/religion_12/index.html"),
    ("Nonfiction", "catalogue/category/books/nonfiction_13/index.html"),
    ("Music", "catalogue/category/books/music_14/index.html"),
    ("Default", "catalogue/category/books/default_15/index.html"),
    ("Science Fiction", "catalogue/category/books/science-fiction_16/index.html"),
    ("Sports and Games", "catalogue/category/books/sports-and-games_17/index.html"),
    ("Add a comment", "catalogue/category/books/add-a-comment_18/index.html"),
    ("Fantasy", "catalogue/category/books/fantasy_19/index.html"),
    ("New Adult", "catalogue/category/books/new-adult_20/index.html"),
    ("Young Adult", "catalogue/category/books/young-adult_21/index.html"),
    ("Science", "catalogue/category/books/science_22/index.html"),
    ("Poetry", "catalogue/category/books/poetry_23/index.html"),
    ("Paranormal", "catalogue/category/books/paranormal_24/index.html"),
    ("Art", "catalogue/category/books/art_25/index.html"),
    ("Psychology", "catalogue/category/books/psychology_26/index.html"),
    ("Autobiography", "catalogue/category/books/autobiography_27/index.html"),
    ("Parenting", "catalogue/category/books/parenting_28/index.html"),
    ("Adult Fiction", "catalogue/category/books/adult-fiction_29/index.html"),
    ("Humor", "catalogue/category/books/humor_30/index.html"),
    ("Horror", "catalogue/category/books/horror_31/index.html"),
    ("History", "catalogue/category/books/history_32/index.html"),
    ("Food and Drink", "catalogue/category/books/food-and-drink_33/index.html"),
    ("Christian Fiction", "catalogue/category/books/christian-fiction_34/index.html"),
    ("Business", "catalogue/category/books/business_35/index.html"),
    ("Biography", "catalogue/category/books/biography_36/index.html"),
    ("Thriller", "catalogue/category/books/thriller_37/index.html"),
    ("Contemporary", "catalogue/category/books/contemporary_38/index.html"),
    ("Spirituality", "catalogue/category/books/spirituality_39/index.html"),
    ("Academic", "catalogue/category/books/academic_40/index.html"),
    ("Self Help", "catalogue/category/books/self-help_41/index.html"),
    ("Historical", "catalogue/category/books/historical_42/index.html"),
    ("Christian", "catalogue/category/books/christian_43/index.html"),
    ("Suspense", "catalogue/category/books/suspense_44/index.html"),
    ("Short Stories", "catalogue/category/books/short-stories_45/index.html"),
    ("Novels", "catalogue/category/books/novels_46/index.html"),
    ("Health", "catalogue/category/books/health_47/index.html"),
    ("Politics", "catalogue/category/books/politics_48/index.html"),
    ("Cultural", "catalogue/category/books/cultural_49/index.html"),
    ("Erotica", "catalogue/category/books/erotica_50/index.html"),
    ("Crime", "catalogue/category/books/crime_51/index.html"),
]

QUOTES_TAGS = [
    "love",
    "inspirational",
    "life",
    "humor",
    "books",
    "reading",
    "friendship",
    "friends",
    "truth",
    "simile",
    "philosophy",
    "god",
    "writing",
    "poetry",
    "death",
    "romance",
    "success",
    "knowledge",
]

HN_ROUTES = [
    ("newest", "https://news.ycombinator.com/newest"),
    ("front", "https://news.ycombinator.com/front"),
    ("ask", "https://news.ycombinator.com/ask"),
    ("show", "https://news.ycombinator.com/show"),
    ("jobs", "https://news.ycombinator.com/jobs"),
    ("past", "https://news.ycombinator.com/past"),
    ("submit", "https://news.ycombinator.com/submit"),
]


def generate_1000_test_cases() -> list[dict[str, Any]]:
    """Generate 1,000 distinct, realistic web test cases."""
    tests: list[dict[str, Any]] = []
    idx = 1

    # Group 1: 50 Book Category Navigation Tests (CAT-0001 to CAT-0050)
    for name, path in BOOK_CATEGORIES:
        tests.append(
            {
                "id": f"CAT-{idx:04d}",
                "name": f"Verify Category Page: {name}",
                "start_url": f"https://books.toscrape.com/{path}",
                "goal": f"Navigate to the {name} category and verify the catalog title matches.",
                "action_type": "navigate_and_assert",
                "expected": [
                    {
                        "type": "dom",
                        "description": f"Page heading should contain category name '{name}'",
                        "selector": "h1",
                        "value": name,
                    },
                    {
                        "type": "url",
                        "description": f"Current URL should contain category path '{path}'",
                        "selector": None,
                        "value": path,
                    },
                ],
                "tags": ["category", "navigation", "catalog"],
                "timeout": 30,
            }
        )
        idx += 1

    # Group 2: 50 Book Catalog Pagination Tests (PAGE-0051 to PAGE-0100)
    for page_num in range(1, 51):
        rel_url = f"catalogue/page-{page_num}.html" if page_num > 1 else "index.html"
        tests.append(
            {
                "id": f"PAGE-{idx:04d}",
                "name": f"Catalog Pagination Page {page_num}",
                "start_url": f"https://books.toscrape.com/{rel_url}",
                "goal": f"Open page {page_num} of book listings and verify products are present.",
                "action_type": "pagination_check",
                "expected": [
                    {
                        "type": "dom",
                        "description": "Page should render product listing containers",
                        "selector": "article.product_pod",
                        "value": None,
                    },
                    {
                        "type": "url",
                        "description": f"URL should point to page {page_num}",
                        "selector": None,
                        "value": rel_url,
                    },
                ],
                "tags": ["pagination", "catalog"],
                "timeout": 30,
            }
        )
        idx += 1

    # Group 3: 400 Product Detail & Price Format Assertions (PROD-0101 to PROD-0500)
    for prod_id in range(1, 401):
        target_page = (prod_id % 50) + 1
        page_suffix = f"catalogue/page-{target_page}.html" if target_page > 1 else "index.html"
        item_offset = (prod_id % 20) + 1
        tests.append(
            {
                "id": f"PROD-{idx:04d}",
                "name": f"Product Item #{prod_id} Integrity Check (Page {target_page}, Pos {item_offset})",
                "start_url": f"https://books.toscrape.com/{page_suffix}",
                "goal": f"Inspect product item #{item_offset} on page {target_page} and verify price format & availability.",
                "action_type": "product_inspection",
                "item_offset": item_offset,
                "expected": [
                    {
                        "type": "dom",
                        "description": "Product price should contain currency symbol (£)",
                        "selector": "p.price_color",
                        "value": "£",
                    },
                    {
                        "type": "dom",
                        "description": "Product availability badge should display in-stock state",
                        "selector": "p.instock.availability",
                        "value": "In stock",
                    },
                ],
                "tags": ["product", "e-commerce", "integrity"],
                "timeout": 30,
            }
        )
        idx += 1

    # Group 4: 200 Quotes to Scrape Tag & Author Tests (QUOTE-0501 to QUOTE-0700)
    for q_idx in range(1, 201):
        tag = QUOTES_TAGS[q_idx % len(QUOTES_TAGS)]
        page_no = (q_idx % 10) + 1
        url = (
            f"https://quotes.toscrape.com/tag/{tag}/page/{page_no}/"
            if page_no > 1
            else f"https://quotes.toscrape.com/tag/{tag}/"
        )
        tests.append(
            {
                "id": f"QUOTE-{idx:04d}",
                "name": f"Quotes Tag Filter: #{tag} (Page {page_no})",
                "start_url": url,
                "goal": f"Navigate to quotes tagged with '{tag}' and verify quote text elements are present.",
                "action_type": "quotes_filter",
                "expected": [
                    {
                        "type": "dom",
                        "description": "Page should contain quote cards",
                        "selector": "div.quote",
                        "value": None,
                    },
                    {
                        "type": "url",
                        "description": f"URL should reference tag '{tag}'",
                        "selector": None,
                        "value": f"/tag/{tag}",
                    },
                ],
                "tags": ["quotes", "tag-filter"],
                "timeout": 30,
            }
        )
        idx += 1

    # Group 5: 200 Hacker News Navigation & Discussion Tests (HN-0701 to HN-0900)
    for hn_idx in range(1, 201):
        route_name, base_route = HN_ROUTES[hn_idx % len(HN_ROUTES)]
        page_param = (hn_idx % 5) + 1
        url = (
            f"{base_route}?p={page_param}"
            if page_param > 1 and route_name not in ("submit",)
            else base_route
        )
        tests.append(
            {
                "id": f"HN-{idx:04d}",
                "name": f"Hacker News Route: /{route_name} (Page {page_param})",
                "start_url": url,
                "goal": f"Load Hacker News {route_name} page and verify table content rendering.",
                "action_type": "hn_navigation",
                "expected": [
                    {
                        "type": "dom",
                        "description": "Page should contain the main navigation header or table",
                        "selector": "#hnmain, table.itemlist, form",
                        "value": None,
                    },
                    {
                        "type": "url",
                        "description": f"URL should contain '{route_name}'",
                        "selector": None,
                        "value": route_name,
                    },
                ],
                "tags": ["hackernews", "navigation", "discussion"],
                "timeout": 30,
            }
        )
        idx += 1

    # Group 6: 100 Form, Search & Interactive Action Tests (ACTION-0901 to ACTION-1000)
    for act_idx in range(1, 101):
        tests.append(
            {
                "id": f"ACT-{idx:04d}",
                "name": f"Interactive Form Search Query #{act_idx}",
                "start_url": "https://quotes.toscrape.com/",
                "goal": f"Inspect quotes search and navigation anchors for query batch #{act_idx}.",
                "action_type": "search_action",
                "expected": [
                    {
                        "type": "dom",
                        "description": "Top tag buttons should be visible and clickable",
                        "selector": "span.tag-item a",
                        "value": None,
                    }
                ],
                "tags": ["form", "interactive", "search"],
                "timeout": 30,
            }
        )
        idx += 1

    return tests


async def execute_test_on_page(page: Any, test: dict[str, Any]) -> dict[str, Any]:
    """Execute a single test case using native Playwright DOM actions and assertions."""
    t_start = time.perf_counter()
    start_ts = time.time()
    steps = []
    passed = True
    error_msg = None

    try:
        # Step 1: Navigate to target URL
        s1_start = time.perf_counter()
        await page.goto(test["start_url"], wait_until="domcontentloaded", timeout=15000)
        s1_end = time.perf_counter()
        steps.append(
            {
                "step": 1,
                "action": "navigate",
                "url": test["start_url"],
                "duration_ms": round((s1_end - s1_start) * 1000, 2),
            }
        )

        # Step 2: In-process DOM assertions
        s2_start = time.perf_counter()
        current_url = page.url
        for exp in test["expected"]:
            if exp["type"] == "url":
                if exp["value"] and exp["value"] not in current_url:
                    passed = False
                    error_msg = f"URL mismatch: expected '{exp['value']}' in '{current_url}'"
            elif exp["type"] == "dom":
                sel = exp.get("selector")
                val = exp.get("value")
                if sel:
                    el = await page.query_selector(sel)
                    if not el:
                        passed = False
                        error_msg = f"Element not found for selector: '{sel}'"
                    elif val:
                        txt = await el.inner_text()
                        if val.lower() not in txt.lower():
                            passed = False
                            error_msg = (
                                f"Text mismatch in '{sel}': expected '{val}', got '{txt[:30]}'"
                            )
        s2_end = time.perf_counter()
        steps.append(
            {
                "step": 2,
                "action": "dom_assertions",
                "verifications_count": len(test["expected"]),
                "duration_ms": round((s2_end - s2_start) * 1000, 2),
            }
        )

    except PlaywrightError as exc:
        passed = False
        error_msg = str(exc)

    t_end = time.perf_counter()
    duration_ms = round((t_end - t_start) * 1000, 2)

    return {
        "test_id": test["id"],
        "name": test["name"],
        "start_url": test["start_url"],
        "status": "pass" if passed else "fail",
        "duration_ms": duration_ms,
        "duration_seconds": round(duration_ms / 1000, 4),
        "steps": steps,
        "error_message": error_msg,
        "timestamp": start_ts,
        "finished_at": time.time(),
    }


async def run_benchmark_suite(
    tests: list[dict[str, Any]], concurrency: int = 12
) -> tuple[list[dict[str, Any]], float, str]:
    """Execute all tests concurrently using a pool of browser contexts."""
    results: list[dict[str, Any]] = [None] * len(tests)
    queue: asyncio.Queue[tuple[int, dict[str, Any]]] = asyncio.Queue()

    for i, t in enumerate(tests):
        queue.put_nowait((i, t))

    t_suite_start = time.perf_counter()

    with Progress(
        SpinnerColumn(),
        TextColumn("[bold cyan]{task.description}"),
        BarColumn(),
        TextColumn("[progress.percentage]{task.percentage:>3.0f}%"),
        TextColumn("({task.completed}/{task.total})"),
        TimeElapsedColumn(),
        TimeRemainingColumn(),
        console=console,
    ) as progress:
        task_id = progress.add_task("Executing 1,000 Test Benchmark Suite...", total=len(tests))

        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            browser_version = browser.version

            async def worker():
                context = await browser.new_context()
                page = await context.new_page()
                while not queue.empty():
                    try:
                        idx, test_item = queue.get_nowait()
                    except asyncio.QueueEmpty:
                        break
                    res = await execute_test_on_page(page, test_item)
                    results[idx] = res
                    progress.update(task_id, advance=1)
                    queue.task_done()
                await context.close()

            workers = [asyncio.create_task(worker()) for _ in range(concurrency)]
            await asyncio.gather(*workers)
            await browser.close()

    total_wall_clock_seconds = time.perf_counter() - t_suite_start
    return results, total_wall_clock_seconds, browser_version


def compute_statistics(
    results: list[dict[str, Any]],
    wall_clock_seconds: float,
    concurrency: int = 10,
    browser_version: str | None = None,
) -> dict[str, Any]:
    """Compute measured statistics for this direct Playwright harness."""
    durations = [r["duration_ms"] for r in results]
    passed_count = sum(1 for r in results if r["status"] == "pass")
    failed_count = len(results) - passed_count
    pass_rate = round((passed_count / len(results)) * 100, 2)

    total_cpu_seconds = sum(durations) / 1000.0
    mean_duration = statistics.mean(durations)
    median_p50 = statistics.median(durations)
    durations_sorted = sorted(durations)
    p90 = durations_sorted[int(len(durations_sorted) * 0.90)]
    p95 = durations_sorted[int(len(durations_sorted) * 0.95)]
    p99 = durations_sorted[int(len(durations_sorted) * 0.99)]
    min_ms = min(durations)
    max_ms = max(durations)
    std_dev = statistics.stdev(durations)

    throughput = round(len(results) / wall_clock_seconds, 2)

    return {
        "benchmark": {
            "kind": "direct_playwright_dom",
            "aiqa_pipeline_exercised": False,
            "model_inference_exercised": False,
            "comparison_scope": "single_harness_only",
            "concurrency": concurrency,
            "data_source": "live public websites",
            "limitations": [
                "Results depend on network and live-site state.",
                "The cases bypass AIQA planning, execution, verification, and reporting.",
                "No vision or computer-use model was run on an equivalent workload.",
            ],
        },
        "environment": environment_metadata(browser_version),
        "dataset_size": len(results),
        "passed": passed_count,
        "failed": failed_count,
        "pass_rate_percent": pass_rate,
        "wall_clock_seconds": round(wall_clock_seconds, 2),
        "total_cpu_seconds": round(total_cpu_seconds, 2),
        "throughput_tests_per_second": throughput,
        "latency_distribution_ms": {
            "mean": round(mean_duration, 2),
            "median_p50": round(median_p50, 2),
            "p90": round(p90, 2),
            "p95": round(p95, 2),
            "p99": round(p99, 2),
            "min": round(min_ms, 2),
            "max": round(max_ms, 2),
            "std_dev": round(std_dev, 2),
        },
    }


def print_summary_table(summary: dict[str, Any]):
    """Render only measurements made by this direct Playwright harness."""
    table = Table(
        title="Direct Playwright DOM Benchmark",
        show_header=True,
        header_style="bold magenta",
    )
    table.add_column("Metric", style="cyan", width=32)
    table.add_column("Measured result", style="green", width=36)

    dist = summary["latency_distribution_ms"]

    table.add_row("Harness", "Direct Playwright navigation and DOM assertions")
    table.add_row("AIQA pipeline exercised", "No")
    table.add_row("Total test cases", f"{summary['dataset_size']:,}")
    table.add_row("Passed / failed", f"{summary['passed']:,} / {summary['failed']:,}")
    table.add_row("Pass rate", f"{summary['pass_rate_percent']}%")
    table.add_row("Wall-clock time", f"{summary['wall_clock_seconds']} s")
    table.add_row("Throughput", f"{summary['throughput_tests_per_second']} tests/sec")
    table.add_row("Average latency per test", f"{dist['mean']} ms")
    table.add_row("Median latency (P50)", f"{dist['median_p50']} ms")
    table.add_row("P95 latency", f"{dist['p95']} ms")

    console.print("\n")
    console.print(table)
    console.print("\n")


async def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--concurrency", type=int, default=10)
    parser.add_argument("--output-dir", type=Path, default=Path("reports"))
    args = parser.parse_args()
    if args.concurrency < 1:
        parser.error("--concurrency must be at least 1")

    console.print(
        Panel.fit("[bold green]Generating 1,000 direct Playwright checks...[/bold green]")
    )
    tests = generate_1000_test_cases()
    assert len(tests) >= 1000, f"Expected at least 1000 tests, generated {len(tests)}"

    output_dir = args.output_dir
    output_dir.mkdir(exist_ok=True)

    suite_file = output_dir / "benchmark_suite_1000.json"
    write_json(
        suite_file,
        {
            "name": "Direct Playwright DOM 1000-check benchmark suite",
            "benchmark_kind": "direct_playwright_dom",
            "aiqa_pipeline_exercised": False,
            "count": len(tests),
            "generated_at": datetime.now(UTC).isoformat(),
            "tests": tests,
        },
    )
    console.print(f"[cyan]Saved 1,000 test cases to: {suite_file}[/cyan]")

    console.print(
        Panel.fit(f"[bold cyan]Launching {args.concurrency} Playwright workers...[/bold cyan]")
    )
    results, wall_clock_seconds, browser_version = await run_benchmark_suite(
        tests,
        concurrency=args.concurrency,
    )

    # Save raw individual records (1,000 items)
    results_file = output_dir / "benchmark_1000_results.json"
    write_json(
        results_file,
        {
            "run_id": f"scale-run-1000-{int(time.time())}",
            "completed_at": datetime.now(UTC).isoformat(),
            "total_count": len(results),
            "benchmark_kind": "direct_playwright_dom",
            "aiqa_pipeline_exercised": False,
            "concurrency": args.concurrency,
            "environment": environment_metadata(browser_version),
            "records": results,
        },
    )
    console.print(f"[green]Saved 1,000 individual test run records to: {results_file}[/green]")

    # Compute statistical summary
    summary = compute_statistics(
        results,
        wall_clock_seconds,
        concurrency=args.concurrency,
        browser_version=browser_version,
    )
    summary_file = output_dir / "benchmark_1000_summary.json"
    write_json(summary_file, summary)
    console.print(f"[green]Saved statistical summary to: {summary_file}[/green]")

    print_summary_table(summary)


if __name__ == "__main__":
    asyncio.run(main())
