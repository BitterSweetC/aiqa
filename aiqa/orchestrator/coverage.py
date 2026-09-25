"""Coverage analyzer matching test suites against the FeatureRegistry."""

from __future__ import annotations

import logging
from typing import Any
from urllib.parse import urljoin, urlparse

from rich import box
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from aiqa.models.coverage import (
    CoverageReport,
    DiscoveredFeature,
    FeatureCoverageSummary,
    FeatureRegistry,
)
from aiqa.models.test_case import TestRunReport, TestSuite

logger = logging.getLogger(__name__)


class CoverageAnalyzer:
    """Calculates feature test coverage and pinpoints uncovered user journeys."""

    def __init__(self, console: Console | None = None) -> None:
        self.console = console or Console()

    def analyze(
        self,
        registry: FeatureRegistry,
        suite: TestSuite,
        run_report: TestRunReport | None = None,
    ) -> CoverageReport:
        """Analyze test coverage of a TestSuite against the site's FeatureRegistry.

        When `run_report` is supplied, also computes execution-verified coverage so only
        features backed by passing, non-inconclusive test results count as verified.
        """
        base_url = getattr(suite, "base_url", "") or registry.base_url
        covered_ids: set[str] = set()
        matched_tests_by_feature: dict[str, list[Any]] = {}

        for feat in registry.features:
            matched: list[Any] = []
            for test in suite.tests:
                if self._matches(feat, test, base_url=base_url, run_report=run_report):
                    matched.append(test)
            if matched:
                covered_ids.add(feat.id)
                matched_tests_by_feature[feat.id] = matched

        verified_ids: set[str] = set()
        failed_ids: set[str] = set()
        inconclusive_ids: set[str] = set()
        unverified_reasons: dict[str, str] = {}

        result_by_id: dict[str, Any] = {}
        if run_report is not None:
            for res in getattr(run_report, "results", []) or []:
                tid = getattr(res, "test_id", None) or getattr(res, "id", None)
                if tid:
                    result_by_id[str(tid)] = res

        blocked_map = dict(getattr(registry, "blocked_routes", None) or {})

        for feat in registry.features:
            if feat.id not in covered_ids:
                feat_norm = self._normalize_url(feat.url, base_url)
                blocked_reason = blocked_map.get(feat.url) or blocked_map.get(feat_norm)
                if blocked_reason:
                    unverified_reasons[feat.id] = (
                        f"Route '{feat.url}' was blocked/inaccessible: {blocked_reason}"
                    )
                else:
                    unverified_reasons[feat.id] = (
                        f"No test in suite covers feature '{feat.name}' on route '{feat.url}'"
                    )
                continue

            if run_report is None:
                continue

            matched_tests = matched_tests_by_feature.get(feat.id, [])
            matched_results = [
                result_by_id[t.id] for t in matched_tests if getattr(t, "id", None) in result_by_id
            ]
            if not matched_results:
                unverified_reasons[feat.id] = (
                    f"Matched test(s) {[t.id for t in matched_tests]} were not executed in run report"
                )
                continue

            passing_non_inconclusive = [
                r
                for r in matched_results
                if getattr(r, "status", "").lower() == "pass"
                and not getattr(r, "inconclusive", False)
            ]
            if passing_non_inconclusive:
                verified_ids.add(feat.id)
                continue

            inconclusive_res = [r for r in matched_results if getattr(r, "inconclusive", False)]
            if inconclusive_res:
                inconclusive_ids.add(feat.id)
                reasons: list[str] = []
                for r in inconclusive_res:
                    reasons.extend(getattr(r, "inconclusive_reasons", None) or [])
                    if not reasons and getattr(r, "error_message", None):
                        reasons.append(str(r.error_message))
                unverified_reasons[feat.id] = (
                    "; ".join(reasons)
                    or "Business rule verification was inconclusive (missing oracle)"
                )
                continue

            failed_ids.add(feat.id)
            fail_msgs = [
                getattr(r, "error_message", None) or f"Test {r.test_id} {r.status}"
                for r in matched_results
            ]
            unverified_reasons[feat.id] = "; ".join(filter(None, fail_msgs))

        untested = [f for f in registry.features if f.id not in covered_ids]
        total_count = len(registry.features)
        covered_count = len(covered_ids)
        verified_count = len(verified_ids)
        rate = (covered_count / total_count) if total_count > 0 else 1.0
        verified_rate = (
            (verified_count / total_count)
            if (run_report is not None and total_count > 0)
            else (1.0 if (run_report is not None and total_count == 0) else 0.0)
        )

        # Breakdown by category
        by_type: dict[str, FeatureCoverageSummary] = {}
        for feat in registry.features:
            ftype = feat.type.lower()
            if ftype not in by_type:
                by_type[ftype] = FeatureCoverageSummary(
                    total=0,
                    covered=0,
                    rate=0.0,
                    verified=0,
                    verified_rate=0.0,
                )
            by_type[ftype].total += 1
            if feat.id in covered_ids:
                by_type[ftype].covered += 1
            if feat.id in verified_ids:
                by_type[ftype].verified += 1

        for summary in by_type.values():
            if summary.total > 0:
                summary.rate = round(summary.covered / summary.total, 3)
                summary.verified_rate = round(summary.verified / summary.total, 3)

        return CoverageReport(
            base_url=registry.base_url,
            total_features=total_count,
            covered_features=covered_count,
            coverage_rate=round(rate, 3),
            verified_features=verified_count,
            verified_coverage_rate=round(verified_rate, 3),
            tested_feature_ids=sorted(covered_ids),
            verified_feature_ids=sorted(verified_ids),
            failed_feature_ids=sorted(failed_ids),
            inconclusive_feature_ids=sorted(inconclusive_ids),
            unverified_reasons=unverified_reasons,
            blocked_routes=blocked_map,
            untested_features=untested,
            by_type=by_type,
            discovered_routes_count=len(registry.routes),
        )

    @staticmethod
    def _normalize_url(url: str | None, base_url: str = "") -> str:
        if not url:
            return ""
        raw = url.strip()
        if not raw:
            return ""
        resolved = urljoin(base_url, raw) if base_url else raw
        parsed = urlparse(resolved)
        path = parsed.path.rstrip("/") or "/"
        if parsed.scheme and parsed.netloc:
            return f"{parsed.scheme}://{parsed.netloc}{path}".rstrip("/")
        return path.rstrip("/")

    def _is_route_compatible(
        self,
        feature: DiscoveredFeature,
        test: Any,
        base_url: str = "",
        run_report: TestRunReport | None = None,
    ) -> bool:
        """Return whether `test` visits or targets the route where `feature` lives."""
        feat_url = self._normalize_url(feature.url, base_url)
        if not feat_url:
            return True

        action_urls: set[str] = set()
        start_url = self._normalize_url(getattr(test, "start_url", ""), base_url)
        if start_url:
            action_urls.add(start_url)

        for action in getattr(test, "actions", None) or []:
            if getattr(action, "action", None) == "navigate" and getattr(action, "url", None):
                action_urls.add(self._normalize_url(action.url, base_url))

        expected_urls: set[str] = set()
        for exp in getattr(test, "expected", None) or []:
            if getattr(exp, "type", "") == "url" and getattr(exp, "value", None):
                val = str(exp.value).strip()
                if val.startswith(("http://", "https://", "/")):
                    expected_urls.add(self._normalize_url(val, base_url))

        if run_report is not None:
            test_id = getattr(test, "id", None)
            for res in getattr(run_report, "results", []) or []:
                if getattr(res, "test_id", None) == test_id:
                    for step in getattr(res, "jev_steps", None) or []:
                        if isinstance(step, dict):
                            for key in ("url", "observed_url", "final_url"):
                                step_u = step.get(key)
                                if isinstance(step_u, str) and step_u:
                                    action_urls.add(self._normalize_url(step_u, base_url))

        candidate_urls = action_urls | expected_urls
        if not candidate_urls:
            return True

        # Element-specific interactive features (search, form, interaction, cart button) must be
        # exercised on a route visited by start_url/navigate/observed step, not merely a destination URL assertion
        if feature.selector and feature.type.lower() in {"search", "form", "interaction"} and action_urls:
            return feat_url in action_urls

        return feat_url in candidate_urls

    def _matches(
        self,
        feature: DiscoveredFeature,
        test: Any,
        base_url: str = "",
        run_report: TestRunReport | None = None,
    ) -> bool:
        """Check if a test case covers a given feature on its route."""
        if not self._is_route_compatible(feature, test, base_url=base_url, run_report=run_report):
            return False

        # Collect selectors explicitly targeted by the test
        test_selectors: set[str] = set()
        for action in getattr(test, "actions", None) or []:
            sel = getattr(action, "selector", None)
            if sel:
                test_selectors.add(sel.strip())
        for exp in getattr(test, "expected", None) or []:
            sel = getattr(exp, "selector", None)
            if sel:
                test_selectors.add(sel.strip())

        # 1. Selector match
        if feature.selector:
            clean_sel = feature.selector.strip().replace('"', "'")
            for sel in test_selectors:
                norm_sel = sel.replace('"', "'")
                if clean_sel in norm_sel or norm_sel in clean_sel:
                    return True
            if clean_sel.lower() in getattr(test, "goal", "").lower().replace('"', "'"):
                return True

        # Interactive element features with an explicit selector require selector or goal evidence;
        # a selector-less navigation test with a matching tag cannot claim coverage for them.
        requires_selector_evidence = bool(
            feature.selector
            and feature.type.lower() in {"search", "form", "interaction", "cart"}
        )
        if requires_selector_evidence:
            return False

        # 2. Tag match (only when route-compatible AND not contradicting an explicit feature selector)
        test_tags = [t.lower() for t in getattr(test, "tags", [])]
        if feature.type.lower() in test_tags and (not feature.selector or not test_selectors):
            return True

        # 3. Name & Goal Semantic keyword matching (only when not contradicting explicit selectors)
        if not feature.selector or not test_selectors:
            feat_name_lower = feature.name.lower()
            test_name_lower = getattr(test, "name", "").lower()
            test_goal_lower = getattr(test, "goal", "").lower()

            keywords = list(
                dict.fromkeys(
                    w
                    for w in feat_name_lower.replace(":", " ").replace("'", " ").split()
                    if len(w) > 3
                )
            )
            match_count = sum(1 for kw in keywords if kw in test_name_lower or kw in test_goal_lower)
            if keywords and match_count >= min(2, len(keywords)):
                return True

        # 4. Route match for route-level features without a specific selector
        if not feature.selector and feature.url:
            feat_path = self._normalize_url(feature.url, base_url)
            test_url = self._normalize_url(getattr(test, "start_url", ""), base_url)
            if feat_path and feat_path == test_url and feature.type.lower() in ("navigation", "checkout", "auth"):
                return True

        return False

    def print_coverage_table(self, report: CoverageReport) -> None:
        """Print a formatted Rich coverage report and gap analysis."""
        cov_pct = f"{report.coverage_rate * 100:.1f}%"
        ver_pct = f"{report.verified_coverage_rate * 100:.1f}%"
        color = (
            "green"
            if report.coverage_rate >= 0.8
            else "yellow"
            if report.coverage_rate >= 0.5
            else "red"
        )

        table = Table(
            title=f"\n[bold]Site Feature Coverage: {report.base_url}[/bold]",
            box=box.ROUNDED,
            header_style="bold cyan",
            show_header=True,
        )
        table.add_column("Category", style="cyan", width=16)
        table.add_column("Features Total", justify="right", width=15)
        table.add_column("Planned/Tested", justify="right", width=14)
        table.add_column("Verified Pass", justify="right", width=14)
        table.add_column("Coverage", justify="right", width=12)

        for cat, summary in sorted(report.by_type.items()):
            c_pct = f"{summary.rate * 100:.0f}%"
            c_style = "green" if summary.rate >= 0.8 else "yellow" if summary.rate >= 0.5 else "red"
            table.add_row(
                cat.capitalize(),
                str(summary.total),
                str(summary.covered),
                str(summary.verified),
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

        if report.blocked_routes:
            blocked_table = Table(
                title="\n[bold yellow]🔒 Blocked / Inaccessible Routes[/bold yellow]",
                box=box.SIMPLE_HEAD,
                header_style="bold yellow",
                show_header=True,
            )
            blocked_table.add_column("Route", style="cyan", min_width=30)
            blocked_table.add_column("Reason", style="yellow", min_width=30)
            for route, reason in list(report.blocked_routes.items())[:8]:
                blocked_table.add_row(route, reason)
            self.console.print(blocked_table)

        self.console.print(
            Panel(
                f"[bold]Discovered Routes:[/bold]   {report.discovered_routes_count} pages\n"
                f"[bold]Discovered Features:[/bold] {report.total_features} total\n"
                f"[bold]Covered Features:[/bold]    {report.covered_features} matched ({cov_pct})\n"
                f"[bold]Verified Features:[/bold]   {report.verified_features} passed ({ver_pct})\n"
                f"[bold {color}]Overall Site Coverage: {cov_pct}[/bold {color}]",
                title=f"[bold {color}]Coverage Summary: {cov_pct}[/bold {color}]",
                border_style=color,
                expand=False,
                padding=(1, 2),
            )
        )
        self.console.print()
