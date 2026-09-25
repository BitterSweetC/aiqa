"""URL and browser state verification for AIQA test execution.

This module provides URL and page title verification, evaluating expectations
against current browser URLs, URL path components, URL query parameters,
regex patterns, and page titles.
"""

from __future__ import annotations

import inspect
import logging
import re
import urllib.parse
from typing import TYPE_CHECKING, Any

from aiqa.models.test_case import Expectation, VerificationResult

if TYPE_CHECKING:
    from playwright.async_api import Page

logger = logging.getLogger(__name__)


def _unwrap_page(page: Any) -> Any:
    """Extract underlying Playwright Page if a BrowserSession is passed."""
    if page is None:
        return None
    prop = getattr(type(page), "page", None)
    if isinstance(prop, property):
        try:
            return page.page
        except Exception:  # noqa: BLE001, S110
            pass
    return page


class UrlVerifier:
    """Verifies test expectations against URL and browser state.

    Supports:
    - Current URL contains substring or specific path
    - Current URL matches regex pattern
    - Current URL exactly equals expected URL
    - Page title contains or matches expected title
    """

    async def verify(
        self,
        expectation: Expectation,
        page: Page | Any,
        **kwargs: Any,
    ) -> VerificationResult:
        """Check URL-based expectations against the current page.

        Supports:
        - Current URL matches pattern
        - URL contains specific path
        - Page title matches

        Args:
            expectation: The test expectation to verify.
            page: Playwright Page instance, BrowserSession, dict, or URL string.
            **kwargs: Extra parameters passed by callers.

        Returns:
            VerificationResult indicating pass/fail status and observed values.
        """
        # Ensure arguments are in expected order even if inverted by caller
        if not isinstance(expectation, Expectation) and isinstance(page, Expectation):
            expectation, page = page, expectation

        try:
            current_url = await self._get_current_url(page)
            page_title = await self._get_page_title(page)
            desc = expectation.description.strip()
            val = expectation.value.strip() if expectation.value is not None else None

            # 1. Page Title Check
            if re.search(r"\bpage\s+title\b|\btitle\s+should\b", desc, re.IGNORECASE):
                return self._verify_title(expectation, page_title, val, desc)

            # 2. URL Check
            return self._verify_url(expectation, current_url, val, desc)

        except Exception as exc:
            logger.exception("Error verifying URL expectation")
            return VerificationResult(
                expectation=expectation,
                passed=False,
                actual_value=None,
                message=f"URL verification encountered an error: {exc}",
            )

    def _verify_title(
        self,
        expectation: Expectation,
        page_title: str,
        val: str | None,
        desc: str,
    ) -> VerificationResult:
        """Verify page title matches or contains expected title."""
        target_val = val
        if not target_val:
            m = re.search(
                r"title\s+should\s+(?:contain|equal|be|match)\s+['\"]?([^'\"]+?)['\"]?\s*$",
                desc,
                re.IGNORECASE,
            )
            if m:
                target_val = m.group(1).strip()
            else:
                m2 = re.search(r"contain\s+['\"]([^'\"]+)['\"]", desc, re.IGNORECASE)
                if m2:
                    target_val = m2.group(1).strip()

        if target_val is None:
            passed = bool(page_title)
            message = f"Page title is '{page_title}'"
        elif "equal" in desc.lower() or "exact" in desc.lower():
            passed = page_title.strip().lower() == target_val.strip().lower()
            message = (
                f"Page title '{page_title}' equals expected '{target_val}'"
                if passed
                else f"Page title '{page_title}' does not equal expected '{target_val}'"
            )
        elif "match" in desc.lower():
            try:
                passed = bool(re.search(target_val, page_title, re.IGNORECASE))
                message = (
                    f"Page title '{page_title}' matches pattern '{target_val}'"
                    if passed
                    else f"Page title '{page_title}' does not match pattern '{target_val}'"
                )
            except re.error:
                passed = target_val.lower() in page_title.lower()
                message = (
                    f"Page title '{page_title}' contains '{target_val}'"
                    if passed
                    else f"Page title '{page_title}' does not contain '{target_val}'"
                )
        else:
            passed = target_val.lower() in page_title.lower()
            message = (
                f"Page title '{page_title}' contains expected '{target_val}'"
                if passed
                else f"Page title '{page_title}' does not contain expected '{target_val}'"
            )

        return VerificationResult(
            expectation=expectation,
            passed=passed,
            actual_value=page_title,
            message=message,
        )

    def _verify_url(
        self,
        expectation: Expectation,
        current_url: str,
        val: str | None,
        desc: str,
    ) -> VerificationResult:
        """Verify current URL against expected pattern, path, or equality."""
        is_negative = bool(
            re.search(r"\bshould\s+not\s+contain\b|\bshould\s+not\s+match\b", desc, re.IGNORECASE)
        )

        # Extract target URL/path/pattern from value or description
        target_val = val
        if not target_val:
            m = re.search(
                r"url\s+should\s+(?:contain|match|equal|be)\s+['\"]?([^'\"\s]+)['\"]?",
                desc,
                re.IGNORECASE,
            )
            if m:
                target_val = m.group(1).strip()
            else:
                m2 = re.search(r"contain\s+['\"]?([^'\"\s]+)['\"]?", desc, re.IGNORECASE)
                if m2:
                    target_val = m2.group(1).strip()

        if target_val is None:
            # If no target specified, verify that URL is a valid non-empty web URL
            passed = bool(current_url and current_url not in ("about:blank", ""))
            return VerificationResult(
                expectation=expectation,
                passed=passed,
                actual_value=current_url,
                message=f"Current URL is '{current_url}'",
            )

        # Pattern match check
        is_pattern = bool(
            "match" in desc.lower()
            or "pattern" in desc.lower()
            or (target_val.startswith("^") or target_val.endswith("$"))
        )

        if is_pattern:
            try:
                matches = bool(re.search(target_val, current_url, re.IGNORECASE))
                passed = not matches if is_negative else matches
                status = "matches" if matches else "does not match"
                message = f"URL '{current_url}' {status} pattern '{target_val}'"
                return VerificationResult(
                    expectation=expectation,
                    passed=passed,
                    actual_value=current_url,
                    message=message,
                )
            except re.error as err:
                logger.warning("Invalid regex '%s' in URL verification: %s", target_val, err)

        # Exact match check
        is_exact = bool("equal" in desc.lower() or "be" in desc.lower())
        if is_exact and target_val.startswith(("http://", "https://")):
            norm_actual = current_url.rstrip("/")
            norm_target = target_val.rstrip("/")
            equals = norm_actual.lower() == norm_target.lower()
            passed = not equals if is_negative else equals
            status_str = "equals" if equals else "does not equal"
            message = f"URL '{current_url}' {status_str} expected '{target_val}'"
            return VerificationResult(
                expectation=expectation,
                passed=passed,
                actual_value=current_url,
                message=message,
            )

        # Path / Substring check (default)
        # Parse path from target if it looks like a path (e.g. "/products" or "cart")
        parsed_current = urllib.parse.urlparse(current_url)
        norm_target_val = target_val.strip("'\"")

        contains = (
            norm_target_val.lower() in current_url.lower()
            or norm_target_val.lower() in parsed_current.path.lower()
            or norm_target_val.lower() in parsed_current.query.lower()
        )

        passed = not contains if is_negative else contains
        if is_negative:
            message = (
                f"URL '{current_url}' does not contain '{norm_target_val}' as expected"
                if passed
                else f"URL '{current_url}' unexpectedly contains '{norm_target_val}'"
            )
        else:
            message = (
                f"URL '{current_url}' contains expected '{norm_target_val}'"
                if passed
                else f"URL '{current_url}' does not contain expected '{norm_target_val}'"
            )

        return VerificationResult(
            expectation=expectation,
            passed=passed,
            actual_value=current_url,
            message=message,
        )

    async def _get_current_url(self, page: Any) -> str:
        """Extract current URL from page object, session, dict, or string."""
        if isinstance(page, str):
            return page

        page_obj = _unwrap_page(page)

        if hasattr(page_obj, "url"):
            url_prop = page_obj.url
            if callable(url_prop):
                res = url_prop()
                val = await res if inspect.isawaitable(res) else res
                return str(val or "")
            return str(url_prop or "")

        if isinstance(page_obj, dict):
            return str(page_obj.get("url", ""))

        return str(page_obj or "")

    async def _get_page_title(self, page: Any) -> str:
        """Extract page title from page object, session, or dict."""
        page_obj = _unwrap_page(page)

        if hasattr(page_obj, "title"):
            try:
                res = page_obj.title()
                title = await res if inspect.isawaitable(res) else res
                return str(title or "").strip()
            except Exception:  # noqa: BLE001, S110
                pass

        if hasattr(page_obj, "evaluate"):
            try:
                res = page_obj.evaluate("() => document.title")
                title = await res if inspect.isawaitable(res) else res
                return str(title or "").strip()
            except Exception:  # noqa: BLE001, S110
                pass

        if isinstance(page_obj, dict):
            return str(page_obj.get("title", "")).strip()

        return ""


__all__ = ["UrlVerifier"]
