"""AIQA Command Line Interface.

Autonomous website testing system powered by AI.
"""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

import click
from rich import box
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from aiqa.models.test_case import TestRunReport, TestSuite, load_test_suite
from aiqa.reports.json_report import JsonReporter

console = Console()


@click.group()
@click.version_option(version="0.1.0", prog_name="AIQA")
def cli() -> None:
    """AIQA — AI-Powered Autonomous Website Testing."""


@cli.command()
@click.option(
    "--url",
    required=True,
    help="Base URL of the website to test",
)
@click.option(
    "--tests",
    required=True,
    type=click.Path(exists=True, dir_okay=False, readable=True),
    help="Path to test suite JSON file",
)
@click.option(
    "--headless/--no-headless",
    default=True,
    help="Run browser in headless mode",
)
@click.option(
    "--cdp",
    default=None,
    help="Connect to existing browser over CDP (e.g. http://127.0.0.1:9222)",
)
@click.option(
    "--profile",
    default=None,
    help="Path to Chrome user data directory for persistent login state",
)
@click.option(
    "--storage-state",
    type=click.Path(exists=True, dir_okay=False, readable=True),
    default=None,
    help="Load cookies and local storage into an isolated browser context",
)
@click.option(
    "--reuse-existing-context",
    is_flag=True,
    help="With --cdp, explicitly share an existing browser context",
)
@click.option(
    "--reuse-existing-page",
    is_flag=True,
    help="With context reuse, explicitly operate on its first existing tab",
)
@click.option(
    "--output",
    default="./reports",
    help="Output directory for reports",
)
@click.option(
    "--screenshots",
    default="./screenshots",
    help="Directory for screenshots",
)
@click.option(
    "--html",
    "html_path",
    default=None,
    help="Path to generate an interactive HTML dashboard",
)
@click.option(
    "--junit",
    "junit_path",
    default=None,
    help="Path to generate a JUnit XML report",
)
def test(
    url: str,
    tests: str,
    headless: bool,
    cdp: str | None,
    profile: str | None,
    storage_state: str | None,
    reuse_existing_context: bool,
    reuse_existing_page: bool,
    output: str,
    screenshots: str,
    html_path: str | None,
    junit_path: str | None,
) -> None:
    """Run test cases against a website."""
    _validate_browser_mode(
        cdp_url=cdp,
        user_data_dir=profile,
        storage_state=storage_state,
        reuse_existing_context=reuse_existing_context,
        reuse_existing_page=reuse_existing_page,
    )
    report = asyncio.run(
        _run_tests(
            url=url,
            tests_path=Path(tests),
            headless=headless,
            cdp_url=cdp,
            user_data_dir=profile,
            storage_state=Path(storage_state) if storage_state else None,
            reuse_existing_context=reuse_existing_context,
            reuse_existing_page=reuse_existing_page,
            output_dir=Path(output),
            screenshots_dir=Path(screenshots),
            html_path=Path(html_path) if html_path else None,
            junit_path=Path(junit_path) if junit_path else None,
        )
    )

    if report is None:
        sys.exit(1)

    summary = getattr(report, "summary", None)
    if summary is not None:
        failed = getattr(summary, "failed", 0)
        errors = getattr(summary, "errors", 0)
        if failed > 0 or errors > 0:
            sys.exit(1)
    else:
        sys.exit(1)


async def _run_tests(
    url: str,
    tests_path: Path | str,
    headless: bool = True,
    cdp_url: str | None = None,
    user_data_dir: Path | str | None = None,
    storage_state: Path | str | None = None,
    reuse_existing_context: bool = False,
    reuse_existing_page: bool = False,
    output_dir: Path | str = "./reports",
    screenshots_dir: Path | str = "./screenshots",
    html_path: Path | str | None = None,
    junit_path: Path | str | None = None,
) -> TestRunReport | None:
    """Execute tests asynchronously, display rich progress, and save reports.

    Args:
        url: Base URL of the website under test.
        tests_path: Path to the test suite JSON file.
        headless: Whether to run the browser in headless mode.
        cdp_url: Optional Chrome DevTools Protocol URL.
        user_data_dir: Optional path to Chrome user data directory.
        storage_state: Optional Playwright storage-state file for an isolated context.
        reuse_existing_context: Whether CDP may share an existing browser context.
        reuse_existing_page: Whether CDP may operate on the first existing tab.
        output_dir: Directory where JSON reports are saved.
        screenshots_dir: Directory where test screenshots are saved.
        html_path: Optional path where interactive HTML report is saved.
        junit_path: Optional path where a JUnit XML report is saved.

    Returns:
        TestRunReport instance on completion, or None on critical error.
    """
    tests_file = Path(tests_path)
    out_path = Path(output_dir)
    screens_path = Path(screenshots_dir)

    # 1. Print AIQA branding header
    _print_header()

    # 2. Load test suite from JSON
    try:
        suite = load_test_suite(tests_file)
    except Exception as exc:  # noqa: BLE001
        console.print(
            Panel(
                f"[bold red]Failed to load test suite from {tests_file}:[/bold red]\n{exc}",
                title="[bold red]Configuration Error[/bold red]",
                border_style="red",
            )
        )
        return None

    # 3. Override base_url if --url provided
    if url:
        old_base = (suite.base_url or "").rstrip("/")
        new_base = url.rstrip("/")
        suite.base_url = new_base

        for test_case in suite.tests:
            if test_case.start_url:
                if old_base and test_case.start_url.startswith(old_base):
                    test_case.start_url = new_base + test_case.start_url[len(old_base) :]
                elif test_case.start_url.startswith("/"):
                    test_case.start_url = f"{new_base}{test_case.start_url}"

    # Print run configuration
    _print_config(suite, tests_file, headless, out_path, screens_path, cdp_url, user_data_dir)

    # 4. Create TestRunner and execute suite
    try:
        from aiqa.orchestrator.runner import TestRunner
    except ImportError as exc:
        console.print(
            Panel(
                f"[bold red]Could not load TestRunner from aiqa.orchestrator.runner:[/bold red]\n{exc}",
                title="[bold red]Import Error[/bold red]",
                border_style="red",
            )
        )
        return None

    try:
        runner = TestRunner(
            headless=headless,
            screenshots_dir=screens_path,
            openai_api_key=os.getenv("OPENAI_API_KEY"),
            console=console,
            cdp_url=cdp_url,
            user_data_dir=user_data_dir,
            storage_state=storage_state,
            reuse_existing_context=reuse_existing_context,
            reuse_existing_page=reuse_existing_page,
        )
    except TypeError:
        if storage_state or reuse_existing_context or reuse_existing_page:
            console.print(
                Panel(
                    "[bold red]The installed TestRunner does not support the requested "
                    "browser-state mode.[/bold red]",
                    title="[bold red]Runner Initialization Error[/bold red]",
                    border_style="red",
                )
            )
            return None
        try:
            runner = TestRunner(
                headless=headless,
                screenshots_dir=screens_path,
                cdp_url=cdp_url,
                user_data_dir=user_data_dir,
            )
        except TypeError:
            try:
                runner = TestRunner(
                    headless=headless,
                    screenshots_dir=screens_path,
                )
            except Exception as exc:  # noqa: BLE001
                console.print(
                    Panel(
                        f"[bold red]Failed to initialize TestRunner:[/bold red]\n{exc}",
                        title="[bold red]Runner Initialization Error[/bold red]",
                        border_style="red",
                    )
                )
                return None

    try:
        report = await runner.run_suite(suite)
    except Exception as exc:  # noqa: BLE001
        err_msg = str(exc)
        if cdp_url and ("ECONNREFUSED" in err_msg or "connect_over_cdp" in err_msg):
            console.print(
                Panel(
                    f"[bold red]Cannot connect to Chrome at {cdp_url}[/bold red]\n\n"
                    "Chrome is not currently running with remote debugging enabled.\n\n"
                    "[bold]To launch Chrome with remote debugging on macOS, run:[/bold]\n"
                    '  [bold white]open -na "Google Chrome" --args --remote-debugging-port=9222 --user-data-dir="/tmp/chrome_aiqa_profile" --no-first-run "https://www.amazon.com"[/bold white]\n\n'
                    "[dim]Or omit --cdp to let AIQA launch its own browser automatically.[/dim]",
                    title="[bold red]CDP Connection Error[/bold red]",
                    border_style="red",
                )
            )
        else:
            console.print(
                Panel(
                    f"[bold red]Test execution failed with unhandled exception:[/bold red]\n{exc}",
                    title="[bold red]Execution Error[/bold red]",
                    border_style="red",
                )
            )
        return None

    # Ensure required report fields are present
    if not getattr(report, "base_url", None):
        report.base_url = suite.base_url
    if not getattr(report, "suite_name", None):
        report.suite_name = suite.name

    # 5. Print summary table
    _print_summary_table(report, suite)

    # 6. Save JSON report and optional HTML dashboard
    saved_html_path: Path | None = None
    saved_junit_path: Path | None = None
    report_write_failed = False
    if html_path:
        from aiqa.reports.html_report import HtmlReporter

        html_reporter = HtmlReporter(output_dir=out_path)
        try:
            saved_html_path = html_reporter.save(report, filename=html_path)
        except Exception as exc:  # noqa: BLE001
            console.print(f"[bold red]Failed to save HTML report:[/bold red] {exc}")
            report_write_failed = True

    if junit_path:
        from aiqa.reports.junit_report import JUnitReporter

        junit_reporter = JUnitReporter(output_dir=out_path)
        try:
            saved_junit_path = junit_reporter.save(report, filename=junit_path)
        except Exception as exc:  # noqa: BLE001
            console.print(f"[bold red]Failed to save JUnit report:[/bold red] {exc}")
            report_write_failed = True

    reporter = JsonReporter(output_dir=out_path)
    saved_path: Path | None = None
    try:
        saved_path = reporter.save(report)
    except Exception as exc:  # noqa: BLE001
        console.print(
            Panel(
                f"[bold red]Failed to save JSON report:[/bold red]\n{exc}",
                title="[bold red]Report Save Error[/bold red]",
                border_style="red",
            )
        )
        report_write_failed = True

    if report_write_failed or saved_path is None:
        return None

    _print_completion_banner(report, saved_path, saved_html_path, saved_junit_path)

    return report


def _print_header() -> None:
    """Print the AIQA branding header panel."""
    console.print()


def _validate_browser_mode(
    *,
    cdp_url: str | None,
    user_data_dir: Path | str | None,
    storage_state: Path | str | None,
    reuse_existing_context: bool,
    reuse_existing_page: bool,
) -> None:
    """Reject browser-state combinations that could be ignored or unsafe."""
    if reuse_existing_page and not reuse_existing_context:
        raise click.UsageError("--reuse-existing-page requires --reuse-existing-context")
    if reuse_existing_context and not cdp_url:
        raise click.UsageError("--reuse-existing-context requires --cdp")
    if storage_state and user_data_dir:
        raise click.UsageError("--storage-state cannot be combined with --profile")
    if storage_state and reuse_existing_context:
        raise click.UsageError(
            "--storage-state cannot be combined with --reuse-existing-context"
        )
    banner = (
        "[bold cyan]  █████╗ ██╗ ██████╗  █████╗ [/bold cyan]\n"
        "[bold cyan] ██╔══██╗██║██╔═══██╗██╔══██╗[/bold cyan]\n"
        "[bold cyan] ███████║██║██║   ██║███████║[/bold cyan]\n"
        "[bold cyan] ██╔══██║██║██║▄▄ ██║██╔══██║[/bold cyan]\n"
        "[bold cyan] ██║  ██║██║╚██████╔╝██║  ██║[/bold cyan]\n"
        "[bold cyan] ╚═╝  ╚═╝╚═╝ ╚══▀▀═╝ ╚═╝  ╚═╝[/bold cyan]\n\n"
        "[bold white]AI-Powered Autonomous Website Testing[/bold white] [dim](v0.1.0)[/dim]\n"
        "[dim]Intelligent Test Planning • Autonomous Browser Automation • Failure Analysis[/dim]"
    )
    console.print(
        Panel(
            banner,
            border_style="cyan",
            expand=False,
            padding=(1, 3),
        )
    )
    console.print()


def _print_config(
    suite: TestSuite,
    tests_path: Path,
    headless: bool,
    output_dir: Path,
    screenshots_dir: Path,
    cdp_url: str | None = None,
    user_data_dir: Path | str | None = None,
) -> None:
    """Print test run configuration details."""
    config_table = Table.grid(padding=(0, 2))
    config_table.add_column(style="bold cyan", no_wrap=True)
    config_table.add_column(style="white")

    config_table.add_row("Target URL:", f"[underline]{suite.base_url}[/underline]")
    config_table.add_row("Suite Name:", suite.name)
    config_table.add_row("Suite File:", str(tests_path))
    config_table.add_row("Tests Loaded:", f"{len(suite.tests)} tests")
    if cdp_url:
        config_table.add_row("Browser:", f"CDP Remote ({cdp_url})")
    elif user_data_dir:
        config_table.add_row("Browser:", f"Persistent Profile ({user_data_dir})")
    else:
        config_table.add_row("Browser:", "Headless" if headless else "Headed (visible)")
    config_table.add_row("Reports Dir:", str(output_dir))
    config_table.add_row("Screenshots:", str(screenshots_dir))

    console.print(config_table)
    console.print()


def _print_summary_table(report: TestRunReport, suite: TestSuite) -> None:
    """Print the final summary table of test results.

    Args:
        report: The executed TestRunReport.
        suite: The source TestSuite for metadata.
    """
    test_name_map: dict[str, str] = {tc.id: tc.name for tc in getattr(suite, "tests", [])}

    table = Table(
        title=f"\n[bold]Suite Results: {report.suite_name}[/bold]",
        box=box.ROUNDED,
        header_style="bold cyan",
        show_header=True,
    )
    table.add_column("Status", justify="center", width=8)
    table.add_column("Test ID", style="cyan", no_wrap=True, width=12)
    table.add_column("Test Name", style="white", min_width=25)
    table.add_column("Duration", justify="right", width=10)
    table.add_column("Details", style="dim", min_width=30)

    status_styles = {
        "pass": "[bold green]PASS[/bold green]",
        "fail": "[bold red]FAIL[/bold red]",
        "error": "[bold magenta]ERROR[/bold magenta]",
        "skip": "[bold yellow]SKIP[/bold yellow]",
    }

    for result in report.results:
        raw_status = getattr(result, "status", "").lower()
        status_display = status_styles.get(raw_status, f"[bold]{raw_status.upper()}[/bold]")

        test_id = getattr(result, "test_id", "")
        test_name = test_name_map.get(test_id, test_id)

        duration_val = getattr(result, "duration_seconds", getattr(result, "duration", 0.0))
        duration_display = "—" if raw_status == "skip" else f"{duration_val:.2f}s"

        details_parts: list[str] = []
        error_msg = getattr(result, "error_message", getattr(result, "error", None))
        if error_msg:
            details_parts.append(error_msg)

        verifications = getattr(
            result, "verification_results", getattr(result, "verifications", [])
        )
        for v in verifications:
            v_passed = getattr(v, "passed", getattr(v, "success", True))
            if not v_passed:
                v_msg = getattr(v, "message", "")
                exp = getattr(v, "expectation", None)
                exp_type = getattr(exp, "type", "") if exp else ""
                details_parts.append(f"[{exp_type}] {v_msg}" if exp_type else v_msg)

        details_str = " | ".join(filter(None, details_parts))
        if len(details_str) > 75:
            details_str = details_str[:72] + "..."

        table.add_row(
            status_display,
            test_id,
            test_name,
            duration_display,
            details_str,
        )

    console.print(table)
    console.print()


def _print_completion_banner(
    report: TestRunReport,
    saved_path: Path,
    html_path: Path | None = None,
    junit_path: Path | None = None,
) -> None:
    """Print the final completion summary and report path."""
    summary = report.summary
    is_success = summary.failed == 0 and summary.errors == 0

    border_color = "green" if is_success else "red"
    title = (
        "[bold green]✓ ALL TESTS PASSED[/bold green]"
        if is_success
        else "[bold red]✗ TEST FAILURES DETECTED[/bold red]"
    )

    pass_rate_pct = f"{summary.pass_rate * 100:.1f}%"
    duration_str = f"{summary.duration_seconds:.2f}s"

    content = (
        f"[bold]Total Tests:[/bold]  {summary.total}\n"
        f"[bold green]Passed:[/bold green]       {summary.passed}\n"
        f"[bold red]Failed:[/bold red]       {summary.failed}\n"
        f"[bold magenta]Errors:[/bold magenta]       {summary.errors}\n"
        f"[bold yellow]Skipped:[/bold yellow]      {summary.skipped}\n"
        f"[bold]Pass Rate:[/bold]    {pass_rate_pct}\n"
        f"[bold]Duration:[/bold]     {duration_str}\n\n"
        f"[bold]JSON Report:[/bold]  [cyan underline]{saved_path.resolve()}[/cyan underline]"
    )
    if html_path:
        content += f"\n[bold]HTML Report:[/bold]  [cyan underline]{html_path.resolve()}[/cyan underline]"
    if junit_path:
        content += f"\n[bold]JUnit Report:[/bold] [cyan underline]{junit_path.resolve()}[/cyan underline]"

    console.print(
        Panel(
            content,
            title=title,
            border_style=border_color,
            expand=False,
            padding=(1, 2),
        )
    )
    console.print()


@cli.command()
@click.option(
    "--url",
    required=True,
    help="Base URL of the website to inspect and plan tests for",
)
@click.option(
    "--goal",
    default=None,
    help="High-level testing goal (e.g. 'Search for office chair and add to cart')",
)
@click.option(
    "--output",
    default="./generated_tests.json",
    help="Output file path for generated JSON test suite",
)
@click.option(
    "--cdp",
    default=None,
    help="Connect to existing browser over CDP (e.g. http://127.0.0.1:9222)",
)
@click.option(
    "--storage-state",
    type=click.Path(exists=True, dir_okay=False, readable=True),
    default=None,
    help="Load cookies and local storage into an isolated browser context",
)
@click.option(
    "--reuse-existing-context",
    is_flag=True,
    help="With --cdp, explicitly share an existing browser context",
)
@click.option(
    "--reuse-existing-page",
    is_flag=True,
    help="With context reuse, explicitly operate on its first existing tab",
)
@click.option(
    "--headless/--no-headless",
    default=True,
    help="Run browser inspection in headless mode",
)
@click.option(
    "--max-tests",
    default=5,
    type=int,
    help="Maximum number of tests to generate",
)
@click.option(
    "--model",
    default=None,
    help="LLM model name to use for planning (defaults to gpt-4o-mini)",
)
def plan(
    url: str,
    goal: str | None,
    output: str,
    cdp: str | None,
    storage_state: str | None,
    reuse_existing_context: bool,
    reuse_existing_page: bool,
    headless: bool,
    max_tests: int,
    model: str | None,
) -> None:
    """Auto-generate structured test cases by inspecting a website with AI."""
    _validate_browser_mode(
        cdp_url=cdp,
        user_data_dir=None,
        storage_state=storage_state,
        reuse_existing_context=reuse_existing_context,
        reuse_existing_page=reuse_existing_page,
    )
    asyncio.run(
        _plan_tests(
            url=url,
            goal=goal,
            output_path=Path(output),
            cdp_url=cdp,
            storage_state=Path(storage_state) if storage_state else None,
            reuse_existing_context=reuse_existing_context,
            reuse_existing_page=reuse_existing_page,
            headless=headless,
            max_tests=max_tests,
            model=model,
        )
    )


async def _plan_tests(
    url: str,
    goal: str | None,
    output_path: Path,
    cdp_url: str | None = None,
    storage_state: Path | str | None = None,
    reuse_existing_context: bool = False,
    reuse_existing_page: bool = False,
    headless: bool = True,
    max_tests: int = 5,
    model: str | None = None,
) -> None:
    """Inspect web page and generate structured test cases."""
    _print_header()
    console.print(f"[bold cyan]🔍 Inspecting website structure:[/bold cyan] {url}")
    if cdp_url:
        console.print(f"  [dim]Attaching to existing browser session via CDP ({cdp_url})[/dim]")

    from aiqa.planner import SiteInspector, TestPlanner

    inspector = SiteInspector(
        headless=headless,
        cdp_url=cdp_url,
        storage_state=storage_state,
        reuse_existing_context=reuse_existing_context,
        reuse_existing_page=reuse_existing_page,
    )
    try:
        inspected = await inspector.inspect(url)
    except Exception as exc:  # noqa: BLE001
        err_msg = str(exc)
        if cdp_url and ("ECONNREFUSED" in err_msg or "connect_over_cdp" in err_msg):
            console.print(
                Panel(
                    f"[bold red]Cannot connect to Chrome at {cdp_url}[/bold red]\n\n"
                    "Chrome is not currently running with remote debugging enabled.\n\n"
                    "[bold]To launch Chrome with remote debugging on macOS, run:[/bold]\n"
                    '  [bold white]open -na "Google Chrome" --args --remote-debugging-port=9222 --user-data-dir="/tmp/chrome_aiqa_profile" --no-first-run "https://www.amazon.com"[/bold white]\n\n'
                    "[dim]Or simply omit the --cdp flag to let AIQA launch its own headless browser automatically.[/dim]",
                    title="[bold red]CDP Connection Error[/bold red]",
                    border_style="red",
                )
            )
        else:
            console.print(
                Panel(
                    f"[bold red]Failed to inspect {url}:[/bold red]\n{exc}",
                    title="[bold red]Inspection Error[/bold red]",
                    border_style="red",
                )
            )
        sys.exit(1)

    console.print(f"[green]✓ Page inspected successfully:[/green] '{inspected.title}'")
    console.print(
        f"  • Discovered: {len(inspected.headings)} headings, "
        f"{len(inspected.search_inputs)} search inputs, "
        f"{len(inspected.buttons)} buttons, "
        f"{len(inspected.nav_links)} navigation links"
    )

    planner = TestPlanner(model=model)
    console.print("\n[bold cyan]🧠 Generating test suite with AI planner...[/bold cyan]")
    if goal:
        console.print(f"  • Goal focus: [italic]{goal}[/italic]")

    try:
        suite = planner.generate_suite(inspected, goal=goal, max_tests=max_tests)
        saved_file = planner.save_suite(suite, output_path)
    except Exception as exc:  # noqa: BLE001
        console.print(
            Panel(
                f"[bold red]Failed to generate test suite:[/bold red]\n{exc}",
                title="[bold red]Planner Error[/bold red]",
                border_style="red",
            )
        )
        sys.exit(1)

    table = Table(
        title=f"\n[bold green]Generated Test Suite: {suite.name}[/bold green]",
        box=box.ROUNDED,
        header_style="bold cyan",
        show_header=True,
    )
    table.add_column("Test ID", style="cyan", no_wrap=True, width=14)
    table.add_column("Test Name", style="white", min_width=25)
    table.add_column("Goal", style="dim", min_width=35)
    table.add_column("Verifications", justify="center", width=14)

    for t in suite.tests:
        g_text = t.goal[:65] + "..." if len(t.goal) > 65 else t.goal
        table.add_row(t.id, t.name, g_text, f"{len(t.expected)} checks")

    console.print(table)
    console.print()

    run_cmd = f"aiqa test --url {url} --tests {saved_file}"
    if cdp_url:
        run_cmd += f" --cdp {cdp_url}"

    console.print(
        Panel(
            f"[bold green]✓ Test suite generated successfully![/bold green]\n\n"
            f"[bold]Tests Generated:[/bold] {len(suite.tests)}\n"
            f"[bold]Saved to:[/bold]         [cyan underline]{saved_file.resolve()}[/cyan underline]\n\n"
            f"[bold]To execute these tests, run:[/bold]\n"
            f"[bold white]  {run_cmd}[/bold white]",
            border_style="green",
            expand=False,
            padding=(1, 2),
        )
    )
    console.print()


@cli.command()
@click.option(
    "--url",
    required=True,
    help="Base URL of the website to crawl and evaluate coverage",
)
@click.option(
    "--tests",
    required=True,
    type=click.Path(exists=True, dir_okay=False, readable=True),
    help="Path to test suite JSON file to evaluate",
)
@click.option(
    "--max-pages",
    default=5,
    type=int,
    help="Maximum number of pages to crawl for feature discovery (default 5)",
)
@click.option(
    "--cdp",
    default=None,
    help="Connect to existing browser over CDP (e.g. http://127.0.0.1:9222)",
)
@click.option(
    "--output",
    default=None,
    help="Optional path to save coverage report JSON",
)
def coverage(
    url: str,
    tests: str,
    max_pages: int,
    cdp: str | None,
    output: str | None,
) -> None:
    """Crawl website, discover features, and report test coverage & gaps."""
    asyncio.run(
        _run_coverage(
            url=url,
            tests_path=Path(tests),
            max_pages=max_pages,
            cdp_url=cdp,
            output_path=Path(output) if output else None,
        )
    )


async def _run_coverage(
    url: str,
    tests_path: Path,
    max_pages: int = 5,
    cdp_url: str | None = None,
    output_path: Path | None = None,
) -> None:
    """Execute site crawl, match tests against features, and display coverage report."""
    _print_header()
    console.print(f"[bold cyan]🕷️ Crawling site routes & discovering features:[/bold cyan] {url}")
    if cdp_url:
        console.print(f"  [dim]Attaching to existing browser session via CDP ({cdp_url})[/dim]")

    from aiqa.crawler import SiteCrawler
    from aiqa.orchestrator.coverage import CoverageAnalyzer

    try:
        suite = load_test_suite(tests_path)
    except Exception as exc:  # noqa: BLE001
        console.print(
            Panel(
                f"[bold red]Failed to load test suite from {tests_path}:[/bold red]\n{exc}",
                title="[bold red]Configuration Error[/bold red]",
                border_style="red",
            )
        )
        sys.exit(1)

    crawler = SiteCrawler(max_pages=max_pages, cdp_url=cdp_url)
    try:
        registry = await crawler.crawl(url)
    except Exception as exc:  # noqa: BLE001
        err_msg = str(exc)
        if cdp_url and ("ECONNREFUSED" in err_msg or "connect_over_cdp" in err_msg):
            console.print(
                Panel(
                    f"[bold red]Cannot connect to Chrome at {cdp_url}[/bold red]\n\n"
                    "Chrome is not currently running with remote debugging enabled.",
                    title="[bold red]CDP Connection Error[/bold red]",
                    border_style="red",
                )
            )
        else:
            console.print(
                Panel(
                    f"[bold red]Crawl failed for {url}:[/bold red]\n{exc}",
                    title="[bold red]Crawl Error[/bold red]",
                    border_style="red",
                )
            )
        sys.exit(1)

    console.print(
        f"[green]✓ Crawl complete![/green] Discovered {len(registry.routes)} routes, "
        f"{len(registry.features)} features.\n"
        "  [dim]Matching test cases against site features...[/dim]"
    )

    analyzer = CoverageAnalyzer(console=console)
    report = analyzer.analyze(registry, suite)
    analyzer.print_coverage_table(report)

    if output_path:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(report.model_dump_json(indent=2), encoding="utf-8")
        console.print(f"[green]Saved coverage report to:[/green] [cyan underline]{output_path.resolve()}[/cyan underline]\n")


@cli.command()
@click.option(
    "--input",
    "input_file",
    required=True,
    type=click.Path(exists=True, dir_okay=False, readable=True),
    help="Path to JSON test run report file to convert",
)
@click.option(
    "--html",
    "html_file",
    default=None,
    help="Output HTML dashboard file path (default: ./reports/dashboard_<timestamp>.html)",
)
def report(input_file: str, html_file: str | None) -> None:
    """Generate an interactive HTML dashboard from a saved JSON test run report."""
    _print_header()
    from aiqa.reports.html_report import HtmlReporter
    from aiqa.reports.json_report import JsonReporter

    try:
        run_report = JsonReporter.load(input_file)
    except Exception as exc:  # noqa: BLE001
        console.print(
            Panel(
                f"[bold red]Failed to load JSON report from {input_file}:[/bold red]\n{exc}",
                title="[bold red]Report Read Error[/bold red]",
                border_style="red",
            )
        )
        sys.exit(1)

    html_reporter = HtmlReporter(output_dir=Path("./reports"))
    try:
        saved_html = html_reporter.save(run_report, filename=html_file)
        console.print(
            Panel(
                f"[bold green]✓ Interactive HTML Dashboard generated successfully![/bold green]\n\n"
                f"[bold]Source Report:[/bold]  {input_file}\n"
                f"[bold]Suite Name:[/bold]     {run_report.suite_name}\n"
                f"[bold]Pass Rate:[/bold]      {run_report.summary.pass_rate * 100:.1f}%\n"
                f"[bold]Total Tests:[/bold]    {run_report.summary.total} (Passed: {run_report.summary.passed}, Failed: {run_report.summary.failed})\n\n"
                f"[bold]HTML Dashboard:[/bold] [cyan underline]{saved_html.resolve()}[/cyan underline]",
                title="[bold green]Report Ready[/bold green]",
                border_style="green",
            )
        )
    except Exception as exc:  # noqa: BLE001
        console.print(
            Panel(
                f"[bold red]Failed to generate HTML dashboard:[/bold red]\n{exc}",
                title="[bold red]Dashboard Error[/bold red]",
                border_style="red",
            )
        )
        sys.exit(1)


@cli.command()
@click.option(
    "--url",
    required=True,
    help="Target website URL to test autonomously",
)
@click.option(
    "--goal",
    default=None,
    help="Optional high-level testing goal (e.g. 'Search for books and click top result')",
)
@click.option(
    "--max-tests",
    default=3,
    type=int,
    help="Maximum number of test cases to generate and run (default: 3)",
)
@click.option(
    "--headless/--no-headless",
    default=True,
    help="Run browser in headless mode (default: True)",
)
@click.option(
    "--cdp",
    default=None,
    help="Connect to existing browser over CDP (e.g. http://127.0.0.1:9222)",
)
@click.option(
    "--storage-state",
    type=click.Path(exists=True, dir_okay=False, readable=True),
    default=None,
    help="Load cookies and local storage into an isolated browser context",
)
@click.option(
    "--reuse-existing-context",
    is_flag=True,
    help="With --cdp, explicitly share an existing browser context",
)
@click.option(
    "--reuse-existing-page",
    is_flag=True,
    help="With context reuse, explicitly operate on its first existing tab",
)
@click.option(
    "--output-dir",
    default="./reports",
    help="Directory for reports and auto-generated test suites",
)
@click.option(
    "--open/--no-open",
    "open_browser",
    default=False,
    help="Automatically open the HTML report in system browser upon completion",
)
@click.option(
    "--junit",
    "junit_path",
    default=None,
    help="Path to generate a JUnit XML report",
)
def auto(
    url: str,
    goal: str | None,
    max_tests: int,
    headless: bool,
    cdp: str | None,
    storage_state: str | None,
    reuse_existing_context: bool,
    reuse_existing_page: bool,
    output_dir: str,
    open_browser: bool,
    junit_path: str | None,
) -> None:
    """Auto-Pilot: Autonomous end-to-end testing from URL to HTML dashboard in one shot."""
    _validate_browser_mode(
        cdp_url=cdp,
        user_data_dir=None,
        storage_state=storage_state,
        reuse_existing_context=reuse_existing_context,
        reuse_existing_page=reuse_existing_page,
    )
    report = asyncio.run(
        _run_auto(
            url=url,
            goal=goal,
            max_tests=max_tests,
            headless=headless,
            cdp_url=cdp,
            storage_state=Path(storage_state) if storage_state else None,
            reuse_existing_context=reuse_existing_context,
            reuse_existing_page=reuse_existing_page,
            output_dir=Path(output_dir),
            open_browser=open_browser,
            junit_path=Path(junit_path) if junit_path else None,
        )
    )
    if report is None:
        sys.exit(1)
    summary = getattr(report, "summary", None)
    if summary is not None:
        failed = getattr(summary, "failed", 0)
        errors = getattr(summary, "errors", 0)
        if failed > 0 or errors > 0:
            sys.exit(1)
    else:
        sys.exit(1)


async def _run_auto(
    url: str,
    goal: str | None = None,
    max_tests: int = 3,
    headless: bool = True,
    cdp_url: str | None = None,
    storage_state: Path | str | None = None,
    reuse_existing_context: bool = False,
    reuse_existing_page: bool = False,
    output_dir: Path = Path("./reports"),
    open_browser: bool = False,
    junit_path: Path | None = None,
) -> TestRunReport | None:
    """Execute end-to-end auto-pilot pipeline."""
    import webbrowser
    from datetime import UTC, datetime

    from aiqa.planner import SiteInspector, TestPlanner

    _print_header()
    console.print(f"[bold cyan]🚀 AIQA Auto-Pilot activated for:[/bold cyan] {url}")
    if goal:
        console.print(f"  • Goal focus: [italic]{goal}[/italic]")
    if cdp_url:
        console.print(f"  • CDP remote attachment: [underline]{cdp_url}[/underline]")
    console.print()

    # Phase 1: Site Inspection & Test Generation
    inspector = SiteInspector(
        headless=headless,
        cdp_url=cdp_url,
        storage_state=storage_state,
        reuse_existing_context=reuse_existing_context,
        reuse_existing_page=reuse_existing_page,
    )
    try:
        inspected = await inspector.inspect(url)
    except Exception as exc:  # noqa: BLE001
        err_msg = str(exc)
        if cdp_url and ("ECONNREFUSED" in err_msg or "connect_over_cdp" in err_msg):
            console.print(
                Panel(
                    f"[bold red]Cannot connect to Chrome at {cdp_url}[/bold red]\n\n"
                    "Chrome is not currently running with remote debugging enabled.",
                    title="[bold red]CDP Connection Error[/bold red]",
                    border_style="red",
                )
            )
        else:
            console.print(
                Panel(
                    f"[bold red]Auto-pilot failed during site inspection:[/bold red]\n{exc}",
                    title="[bold red]Inspection Error[/bold red]",
                    border_style="red",
                )
            )
        return None

    planner = TestPlanner()
    suite = planner.generate_suite(inspected, goal=goal, max_tests=max_tests)

    ts_str = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
    output_dir.mkdir(parents=True, exist_ok=True)
    suite_file = output_dir / f"auto_suite_{ts_str}.json"
    planner.save_suite(suite, suite_file)

    console.print(f"[green]✓ Generated {len(suite.tests)} tests saved to:[/green] [dim]{suite_file}[/dim]\n")
    console.print("[bold cyan]⚡ Executing test suite in real browser...[/bold cyan]\n")

    html_report_file = output_dir / f"dashboard_{ts_str}.html"

    # Phase 2: Execute suite and generate HTML dashboard
    report = await _run_tests(
        url=url,
        tests_path=suite_file,
        headless=headless,
        cdp_url=cdp_url,
        storage_state=storage_state,
        reuse_existing_context=reuse_existing_context,
        reuse_existing_page=reuse_existing_page,
        output_dir=output_dir,
        html_path=html_report_file,
        junit_path=junit_path,
    )

    if report is not None and open_browser:
        try:
            webbrowser.open(f"file://{html_report_file.resolve()}")
            console.print(f"[bold green]✓ Opened dashboard in browser:[/bold green] {html_report_file.resolve()}\n")
        except Exception as exc:  # noqa: BLE001
            console.print(f"[yellow]Could not open the dashboard automatically:[/yellow] {exc}")

    return report


if __name__ == "__main__":
    cli()
