"""Coverage analyzer matching test suites against the FeatureRegistry."""

from __future__ import annotations

import logging
from typing import Any

from rich import box
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from aiqa.models.coverage import CoverageReport, DiscoveredFeature, FeatureCoverageSummary, FeatureRegistry
from aiqa.models.test_case import TestSuite

logger = logging.getLogger(__name__)


class CoverageAnalyzer:
    """Calculates feature test coverage and pinpoints uncovered user journeys."""

    def __init__(self, console: Console | None = None) -> None:
        self.console = console or Console()

    def analyze(self, registry: FeatureRegistry, suite: TestSuite) -> CoverageReport:
        """Analyze test coverage of a TestSuite against the site's FeatureRegistry."""
        covered_ids: set[str] = set()

        for feat in registry.features:
            for test in suite.tests:
                if self._matches(feat, test):
                    covered_ids.add(feat.id)
                    break

        untested = [f for f in registry.features if f.id not in covered_ids]
        total_count = len(registry.features)
        covered_count = len(covered_ids)
        rate = (covered_count / total_count) if total_count > 0 else 1.0

        # Breakdown by category
        by_type: dict[str, FeatureCoverageSummary] = {}
        for feat in registry.features:
            ftype = feat.type.lower()
            if ftype not in by_type:
                by_type[ftype] = FeatureCoverageSummary(total=0, covered=0, rate=0.0)
            by_type[ftype].total += 1
            if feat.id in covered_ids:
                by_type[ftype].covered += 1

        for ftype, summary in by_type.items():
            if summary.total > 0:
                summary.rate = round(summary.covered / summary.total, 3)

        return CoverageReport(
            base_url=registry.base_url,
            total_features=total_count,
            covered_features=covered_count,
            coverage_rate=round(rate, 3),
            tested_feature_ids=sorted(list(covered_ids)),
            untested_features=untested,
            by_type=by_type,
            discovered_routes_count=len(registry.routes),
        )

    def _matches(self, feature: DiscoveredFeature, test: Any) -> bool:
        """Check if a test case covers a given feature."""
        # 1. Selector match
        if feature.selector:
            clean_sel = feature.selector.strip()
            # Check expectations
            for exp in getattr(test, "expected", []):
                if getattr(exp, "selector", None) and clean_sel in exp.selector:
                    return True
            # Check goal text
            if clean_sel.lower() in getattr(test, "goal", "").lower():
                return True

        # 2. Tag match
        test_tags = [t.lower() for t in getattr(test, "tags", [])]
        if feature.type.lower() in test_tags:
            return True

        # 3. Name & Goal Semantic keyword matching
        feat_name_lower = feature.name.lower()
        test_name_lower = getattr(test, "name", "").lower()
        test_goal_lower = getattr(test, "goal", "").lower()

        keywords = [w for w in feat_name_lower.replace(":", " ").replace("'", " ").split() if len(w) > 3]
        match_count = sum(1 for kw in keywords if kw in test_name_lower or kw in test_goal_lower)
        if keywords and match_count >= min(2, len(keywords)):
            return True

        # 4. Route match for specialized paths
        if feature.url and feature.url != getattr(test, "start_url", ""):
            feat_path = feature.url.rstrip("/")
            test_url = getattr(test, "start_url", "").rstrip("/")
            if feat_path and feat_path == test_url:
                return True

        return False

    def print_coverage_table(self, report: CoverageReport) -> None:
        """Print a formatted Rich coverage report and gap analysis."""
        cov_pct = f"{report.coverage_rate * 100:.1f}%"
        color = "green" if report.coverage_rate >= 0.8 else "yellow" if report.coverage_rate >= 0.5 else "red"

        table = Table(
            title=f"\n[bold]Site Feature Coverage: {report.base_url}[/bold]",
            box=box.ROUNDED,
            header_style="bold cyan",
            show_header=True,
        )
        table.add_column("Category", style="cyan", width=16)
        table.add_column("Features Total", justify="right", width=15)
        table.add_column("Tested", justify="right", width=12)
        table.add_column("Coverage", justify="right", width=12)

        for cat, summary in sorted(report.by_type.items()):
            c_pct = f"{summary.rate * 100:.0f}%"
            c_style = "green" if summary.rate >= 0.8 else "yellow" if summary.rate >= 0.5 else "red"
            table.add_row(
                cat.capitalize(),
                str(summary.total),
                str(summary.covered),
                f"[{c_style}]{c_pct}[/{c_style}]",
            )

        self.console.print(table)

        # Print Untested Gaps
        if report.untested_features:
            gap_table = Table(
                title="\n[bold red]⚠️ Untested Feature Gaps Detected[/bold red]",
                box=box.SIMPLE_HEAD,
                header_style="bold red",
                show_header=True,
            )
            gap_table.add_column("Feature ID", style="dim", width=18)
            gap_table.add_column("Type", style="yellow", width=12)
            gap_table.add_column("Untested Feature Name", style="white", min_width=30)
            gap_table.add_column("Route", style="dim", min_width=25)

            for feat in report.untested_features[:8]:
                gap_table.add_row(feat.id, feat.type, feat.name, feat.url)

            self.console.print(gap_table)

        self.console.print(
            Panel(
                f"[bold]Discovered Routes:[/bold] {report.discovered_routes_count} pages\n"
                f"[bold]Discovered Features:[/bold] {report.total_features} total\n"
                f"[bold]Covered Features:[/bold]    {report.covered_features} tested\n"
                f"[bold {color}]Overall Site Coverage: {cov_pct}[/bold {color}]",
                title=f"[bold {color}]Coverage Summary: {cov_pct}[/bold {color}]",
                border_style=color,
                expand=False,
                padding=(1, 2),
            )
        )
        self.console.print()
