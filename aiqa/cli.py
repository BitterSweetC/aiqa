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
@click.option(
    "--allow-origin",
    "allowed_origins",
    multiple=True,
    help="Additional origin(s) permitted for navigation",
)
@click.option(
    "--allow-cross-origin",
    is_flag=True,
    help="Explicitly allow cross-origin navigation outside configured origins",
)
@click.option(
    "--select",
    "select_ids",
    multiple=True,
    help="Select specific test case ID(s) to run",
)
@click.option(
    "--tag",
    "tags",
    multiple=True,
    help="Select test cases matching tag(s)",
)
@click.option(
    "--shard",
    default=None,
    help="Deterministic shard specification INDEX/TOTAL (e.g. 1/4)",
)
@click.option(
    "--workers",
    default=1,
    type=int,
    help="Bounded concurrent browser workers (1-32, default: 1)",
)
@click.option(
    "--retries",
    "max_retries",
    default=0,
    type=int,
    help="Max retries for transient infrastructure failures (0-5, default: 0)",
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
    allowed_origins: tuple[str, ...] = (),
    allow_cross_origin: bool = False,
    select_ids: tuple[str, ...] = (),
    tags: tuple[str, ...] = (),
    shard: str | None = None,
    workers: int = 1,
    max_retries: int = 0,
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
            allowed_origins=list(allowed_origins),
            allow_cross_origin=allow_cross_origin,
            select_ids=list(select_ids),
            tags=list(tags),
            shard=shard,
            workers=workers,
            max_retries=max_retries,
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
    allowed_origins: list[str] | tuple[str, ...] = (),
    allow_cross_origin: bool = False,
    select_ids: list[str] | tuple[str, ...] = (),
    tags: list[str] | tuple[str, ...] = (),
    shard: str | None = None,
    workers: int = 1,
    max_retries: int = 0,
) -> TestRunReport | None:
    """Execute tests asynchronously, display rich progress, and save reports."""
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

    if allowed_origins:
        suite.allowed_origins = list(dict.fromkeys([*suite.allowed_origins, *allowed_origins]))
    if allow_cross_origin:
        suite.allow_cross_origin = True

    if select_ids or tags or shard:
        from aiqa.orchestrator.runner import select_tests

        try:
            suite = select_tests(
                suite,
                select_ids=list(select_ids),
                tags=list(tags),
                shard=shard,
            )
        except Exception as exc:  # noqa: BLE001
            console.print(
                Panel(
                    f"[bold red]Invalid test selection or shard configuration:[/bold red]\n{exc}",
                    title="[bold red]Selection Error[/bold red]",
                    border_style="red",
                )
            )
            return None

    from aiqa.security.policy import validate_test_suite_policy

    policy_errors = validate_test_suite_policy(suite)
    if policy_errors:
        console.print(
            Panel(
                "[bold red]Suite policy validation failed before browser launch:[/bold red]\n"
                + "\n".join(f"• {err}" for err in policy_errors),
                title="[bold red]Policy Validation Error[/bold red]",
                border_style="red",
            )
        )
        return None

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
        runner_kwargs: dict[str, object] = {
            "headless": headless,
            "screenshots_dir": screens_path,
            "openai_api_key": os.getenv("OPENAI_API_KEY"),
            "console": console,
            "cdp_url": cdp_url,
            "user_data_dir": user_data_dir,
            "storage_state": storage_state,
            "reuse_existing_context": reuse_existing_context,
            "reuse_existing_page": reuse_existing_page,
        }
        if allowed_origins:
            runner_kwargs["allowed_origins"] = list(allowed_origins)
        if allow_cross_origin:
            runner_kwargs["allow_cross_origin"] = True
        if workers != 1:
            runner_kwargs["workers"] = workers
        if max_retries != 0:
            runner_kwargs["max_retries"] = max_retries
        runner = TestRunner(**runner_kwargs)  # type: ignore[arg-type]
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
@click.option(
    "--allow-origin",
    "allowed_origins",
    multiple=True,
    help="Additional origin(s) permitted for planned navigation",
)
@click.option(
    "--allow-cross-origin",
    is_flag=True,
    help="Explicitly allow cross-origin navigation links in generated tests",
)
@click.option(
    "--role",
    default=None,
    help="Target RBAC role name to bind to generated test cases (e.g. admin, guest, member)",
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
    allowed_origins: tuple[str, ...] = (),
    allow_cross_origin: bool = False,
    role: str | None = None,
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
            allowed_origins=list(allowed_origins),
            allow_cross_origin=allow_cross_origin,
            role=role,
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
    allowed_origins: list[str] | tuple[str, ...] = (),
    allow_cross_origin: bool = False,
    role: str | None = None,
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

    planner = TestPlanner(
        model=model,
        allowed_origins=list(allowed_origins),
        allow_cross_origin=allow_cross_origin,
    )
    console.print("\n[bold cyan]🧠 Generating test suite with AI planner...[/bold cyan]")
    if goal:
        console.print(f"  • Goal focus: [italic]{goal}[/italic]")

    try:
        suite = planner.generate_suite(
            inspected,
            goal=goal,
            max_tests=max_tests,
            allowed_origins=list(allowed_origins),
            allow_cross_origin=allow_cross_origin,
            role=role,
        )
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

    if getattr(suite, "planning_notes", None):
        console.print("[bold yellow]Planning Notes & Omissions:[/bold yellow]")
        for note in suite.planning_notes:
            console.print(f"  [yellow]• {note}[/yellow]")
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
    "--report",
    "report_path",
    default=None,
    type=click.Path(exists=True, dir_okay=False, readable=True),
    help="Optional path to a saved TestRunReport JSON to compute execution-verified coverage",
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
    report_path: str | None,
    max_pages: int,
    cdp: str | None,
    output: str | None,
) -> None:
    """Crawl website, discover features, and report test coverage & gaps."""
    asyncio.run(
        _run_coverage(
            url=url,
            tests_path=Path(tests),
            report_path=Path(report_path) if report_path else None,
            max_pages=max_pages,
            cdp_url=cdp,
            output_path=Path(output) if output else None,
        )
    )


async def _run_coverage(
    url: str,
    tests_path: Path,
    report_path: Path | None = None,
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
    from aiqa.reports.json_report import JsonReporter

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

    run_report: TestRunReport | None = None
    if report_path is not None:
        try:
            run_report = JsonReporter.load(report_path)
        except Exception as exc:  # noqa: BLE001
            console.print(
                Panel(
                    f"[bold red]Failed to load execution report from {report_path}:[/bold red]\n{exc}",
                    title="[bold red]Report Load Error[/bold red]",
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
    report = analyzer.analyze(registry, suite, run_report=run_report)
    analyzer.print_coverage_table(report)

    if output_path:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(report.model_dump_json(indent=2), encoding="utf-8")
        console.print(f"[green]Saved coverage report to:[/green] [cyan underline]{output_path.resolve()}[/cyan underline]\n")


@cli.command()
@click.option(
    "--input",
    "input_files",
    required=True,
    multiple=True,
    type=click.Path(exists=True, dir_okay=False, readable=True),
    help="Path(s) to JSON test run report file(s) to convert or aggregate",
)
@click.option(
    "--html",
    "html_file",
    default=None,
    help="Output HTML dashboard file path (default: ./reports/dashboard_<timestamp>.html)",
)
@click.option(
    "--output-json",
    "json_file",
    default=None,
    help="Optional path to save aggregated JSON report",
)
@click.option(
    "--junit",
    "junit_file",
    default=None,
    help="Optional path to save JUnit XML report",
)
def report(
    input_files: tuple[str, ...],
    html_file: str | None,
    json_file: str | None = None,
    junit_file: str | None = None,
) -> None:
    """Generate an interactive HTML dashboard (and optional aggregated JSON/JUnit) from saved report(s)."""
    _print_header()
    from aiqa.reports.html_report import HtmlReporter
    from aiqa.reports.json_report import JsonReporter
    from aiqa.reports.junit_report import JUnitReporter

    try:
        loaded_reports = [JsonReporter.load(p) for p in input_files]
        if len(loaded_reports) == 1:
            run_report = loaded_reports[0]
        else:
            run_report = TestRunReport.aggregate(loaded_reports)
    except Exception as exc:  # noqa: BLE001
        console.print(
            Panel(
                f"[bold red]Failed to load or aggregate JSON report(s) from {', '.join(input_files)}:[/bold red]\n{exc}",
                title="[bold red]Report Read Error[/bold red]",
                border_style="red",
            )
        )
        sys.exit(1)

    html_reporter = HtmlReporter(output_dir=Path("./reports"))
    try:
        if json_file:
            JsonReporter(output_dir=Path("./reports")).save(run_report, filename=json_file)
        if junit_file:
            JUnitReporter(output_dir=Path("./reports")).save(run_report, filename=junit_file)
        saved_html = html_reporter.save(run_report, filename=html_file)
        console.print(
            Panel(
                f"[bold green]✓ Interactive HTML Dashboard generated successfully![/bold green]\n\n"
                f"[bold]Source Report(s):[/bold] {', '.join(input_files)}\n"
                f"[bold]Suite Name:[/bold]       {run_report.suite_name}\n"
                f"[bold]Pass Rate:[/bold]        {run_report.summary.pass_rate * 100:.1f}%\n"
                f"[bold]Total Tests:[/bold]      {run_report.summary.total} (Passed: {run_report.summary.passed}, Failed: {run_report.summary.failed})\n\n"
                f"[bold]HTML Dashboard:[/bold]   [cyan underline]{saved_html.resolve()}[/cyan underline]",
                title="[bold green]Report Ready[/bold green]",
                border_style="green",
            )
        )
    except Exception as exc:  # noqa: BLE001
        console.print(
            Panel(
                f"[bold red]Failed to generate report artifacts:[/bold red]\n{exc}",
                title="[bold red]Dashboard Error[/bold red]",
                border_style="red",
            )
        )
        sys.exit(1)


@cli.command()
@click.option(
    "--workers",
    default=2,
    type=int,
    help="Bounded concurrent browser workers (1-32, default: 2)",
)
@click.option(
    "--warmup",
    default=1,
    type=int,
    help="Number of warmup runs before measurement (default: 1)",
)
@click.option(
    "--scale",
    default=1,
    type=int,
    help="Multiplier to replicate the frozen benchmark suite (default: 1)",
)
@click.option(
    "--compare-baseline",
    is_flag=True,
    help="Compare against equivalent direct Playwright baseline under identical concurrency",
)
@click.option(
    "--evaluate-defects",
    is_flag=True,
    help="Evaluate seeded defect-detection recall, false-alarm rate, diagnosis accuracy, and consistency",
)
@click.option(
    "--output-dir",
    default="./reports/benchmarks",
    help="Directory to write benchmark artifacts",
)
def benchmark(
    workers: int,
    warmup: int,
    scale: int,
    compare_baseline: bool,
    evaluate_defects: bool,
    output_dir: str,
) -> None:
    """Run the reproducible end-to-end AIQA pipeline benchmark."""
    from aiqa.benchmarks.harness import BenchmarkHarness

    harness = BenchmarkHarness(output_dir=Path(output_dir), headless=True)
    bench_report = asyncio.run(
        harness.run(
            workers=workers,
            warmup_runs=warmup,
            scale_multiplier=scale,
            compare_baseline=compare_baseline,
            evaluate_defects=evaluate_defects,
        )
    )
    console.print_json(bench_report.model_dump_json(indent=2))
    if bench_report.failed_tests > 0 or bench_report.error_tests > 0:
        sys.exit(1)


@cli.command()
@click.option(
    "--dir",
    "target_dir",
    default="./reports",
    help="Artifact directory to prune (default: ./reports)",
)
@click.option(
    "--max-age-days",
    default=30.0,
    type=float,
    help="Maximum age in days before artifacts are pruned (default: 30)",
)
@click.option(
    "--max-files",
    default=None,
    type=int,
    help="Optional maximum number of newest artifacts to retain",
)
@click.option(
    "--dry-run",
    is_flag=True,
    help="List files that would be pruned without deleting them",
)
def retention(
    target_dir: str,
    max_age_days: float,
    max_files: int | None,
    dry_run: bool,
) -> None:
    """Enforce retention bounds on report, screenshot, and audit log directories."""
    import json

    from aiqa.security.retention import RetentionManager

    mgr = RetentionManager(max_age_days=max_age_days, max_files=max_files)
    result = mgr.prune(Path(target_dir), dry_run=dry_run)
    console.print_json(json.dumps(result, indent=2))


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
    "--max-pages",
    default=5,
    type=int,
    help="Maximum number of routes to crawl for multi-page feature discovery (default: 5)",
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
@click.option(
    "--role",
    default=None,
    help="Target RBAC role name to bind to generated test cases (e.g. admin, guest, member)",
)
def auto(
    url: str,
    goal: str | None,
    max_tests: int,
    max_pages: int,
    headless: bool,
    cdp: str | None,
    storage_state: str | None,
    reuse_existing_context: bool,
    reuse_existing_page: bool,
    output_dir: str,
    open_browser: bool,
    junit_path: str | None,
    role: str | None = None,
) -> None:
    """Auto-Pilot: Closed-loop multi-page exploration, goal planning, execution, coverage, and gap follow-up."""
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
            max_pages=max_pages,
            headless=headless,
            cdp_url=cdp,
            storage_state=Path(storage_state) if storage_state else None,
            reuse_existing_context=reuse_existing_context,
            reuse_existing_page=reuse_existing_page,
            output_dir=Path(output_dir),
            open_browser=open_browser,
            junit_path=Path(junit_path) if junit_path else None,
            role=role,
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
    max_pages: int = 5,
    headless: bool = True,
    cdp_url: str | None = None,
    storage_state: Path | str | None = None,
    reuse_existing_context: bool = False,
    reuse_existing_page: bool = False,
    output_dir: Path = Path("./reports"),
    open_browser: bool = False,
    junit_path: Path | None = None,
    role: str | None = None,
) -> TestRunReport | None:
    """Execute end-to-end closed-loop auto-pilot pipeline."""
    import inspect as py_inspect
    import webbrowser
    from datetime import UTC, datetime

    from aiqa.crawler import SiteCrawler
    from aiqa.orchestrator.coverage import CoverageAnalyzer
    from aiqa.planner import SiteInspector, TestPlanner

    _print_header()
    console.print(f"[bold cyan]🚀 AIQA Auto-Pilot activated for:[/bold cyan] {url}")
    if goal:
        console.print(f"  • Goal focus: [italic]{goal}[/italic]")
    if cdp_url:
        console.print(f"  • CDP remote attachment: [underline]{cdp_url}[/underline]")
    console.print()

    # Phase 1: Site Inspection & Multi-Page Feature Discovery
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

    registry = None
    if hasattr(inspected, "url") and max_pages > 1:
        try:
            crawler = SiteCrawler(
                max_pages=max_pages,
                headless=headless,
                cdp_url=cdp_url,
                storage_state=storage_state,
            )
            registry = await crawler.crawl(url)
            console.print(
                f"[green]✓ Crawled {len(registry.routes)} route(s)[/green] "
                f"and discovered {len(registry.features)} feature(s)"
                + (f" ({len(registry.blocked_routes)} blocked route(s))" if registry.blocked_routes else "")
            )
        except Exception as exc:  # noqa: BLE001
            console.print(f"[yellow]⚠ Multi-page crawl skipped:[/yellow] {exc}")

    # Phase 2: Goal-Driven & Multi-Route Test Planning (reserve budget for Phase 4 gap recovery)
    planner = TestPlanner()
    sig = py_inspect.signature(planner.generate_suite)
    has_var_kw = any(
        p.kind == py_inspect.Parameter.VAR_KEYWORD for p in sig.parameters.values()
    )
    initial_max_tests = (
        max_tests - 1
        if (registry is not None and getattr(registry, "features", None) and max_tests >= 3)
        else max_tests
    )
    plan_kwargs: dict[str, object] = {"goal": goal, "max_tests": initial_max_tests}
    if "registry" in sig.parameters or has_var_kw:
        plan_kwargs["registry"] = registry
    if "include_gap_fill" in sig.parameters or has_var_kw:
        plan_kwargs["include_gap_fill"] = False
    if "role" in sig.parameters or has_var_kw:
        plan_kwargs["role"] = role
    suite = planner.generate_suite(inspected, **plan_kwargs)

    ts_str = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
    output_dir.mkdir(parents=True, exist_ok=True)
    suite_file = output_dir / f"auto_suite_{ts_str}.json"
    planner.save_suite(suite, suite_file)

    console.print(f"[green]✓ Generated {len(suite.tests)} tests saved to:[/green] [dim]{suite_file}[/dim]\n")
    console.print("[bold cyan]⚡ Executing test suite in real browser...[/bold cyan]\n")

    html_report_file = output_dir / f"dashboard_{ts_str}.html"

    # Phase 3: Execute suite and generate HTML dashboard
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

    # Phase 4: Closed-Loop Coverage Evaluation & Gap Follow-Up
    if registry is not None and report is not None and hasattr(report, "results"):
        analyzer = CoverageAnalyzer(console=console)
        cov_report = analyzer.analyze(registry, suite, run_report=report)
        remaining_budget = max(0, max_tests - len(suite.tests))
        if (
            cov_report.untested_features
            and remaining_budget > 0
            and hasattr(planner, "generate_gap_tests")
        ):
            gap_tests = planner.generate_gap_tests(
                registry=registry,
                uncovered_features=cov_report.untested_features,
                existing_tests=suite.tests,
                max_new_tests=remaining_budget,
            )
            if gap_tests:
                if role:
                    for gt in gap_tests:
                        if not gt.role:
                            gt.role = role
                console.print(
                    f"[bold cyan]🔄 Closed-loop gap recovery:[/bold cyan] "
                    f"generated {len(gap_tests)} follow-up test(s) for uncovered features..."
                )
                gap_suite = suite.model_copy(
                    update={
                        "name": f"{suite.name} (Gap Recovery)",
                        "tests": gap_tests,
                    }
                )
                gap_suite_file = output_dir / f"auto_gap_suite_{ts_str}.json"
                planner.save_suite(gap_suite, gap_suite_file)

                suite.tests.extend(gap_tests)
                planner.save_suite(suite, suite_file)

                gap_report = await _run_tests(
                    url=url,
                    tests_path=gap_suite_file,
                    headless=headless,
                    cdp_url=cdp_url,
                    storage_state=storage_state,
                    reuse_existing_context=reuse_existing_context,
                    reuse_existing_page=reuse_existing_page,
                    output_dir=output_dir,
                    html_path=html_report_file,
                    junit_path=junit_path,
                )
                if gap_report is not None and hasattr(gap_report, "results"):
                    from aiqa.reports.html_report import HtmlReporter
                    from aiqa.reports.junit_report import JUnitReporter

                    merged_results = [*report.results, *gap_report.results]
                    report = TestRunReport.create(
                        run_id=report.run_id,
                        suite_name=suite.name,
                        base_url=report.base_url,
                        started_at=report.started_at,
                        finished_at=gap_report.finished_at,
                        results=merged_results,
                    )
                    try:
                        HtmlReporter(output_dir=output_dir).save(report, filename=html_report_file)
                        if junit_path is not None:
                            JUnitReporter(output_dir=output_dir).save(report, filename=junit_path)
                    except Exception:  # noqa: BLE001, S110
                        pass
                    cov_report = analyzer.analyze(registry, suite, run_report=report)

        cov_file = output_dir / f"auto_coverage_{ts_str}.json"
        cov_file.write_text(cov_report.model_dump_json(indent=2), encoding="utf-8")
        analyzer.print_coverage_table(cov_report)

    if report is not None and open_browser:
        try:
            webbrowser.open(f"file://{html_report_file.resolve()}")
            console.print(f"[bold green]✓ Opened dashboard in browser:[/bold green] {html_report_file.resolve()}\n")
        except Exception as exc:  # noqa: BLE001
            console.print(f"[yellow]Could not open the dashboard automatically:[/yellow] {exc}")

    return report


if __name__ == "__main__":
    cli()
