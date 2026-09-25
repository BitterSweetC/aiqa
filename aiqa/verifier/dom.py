"""DOM-based verification for AIQA test execution.

This module provides DOM state verification using Playwright, evaluating
expectations against page element existence, text content, element counts,
attributes, and document titles.
"""

from __future__ import annotations

import inspect
import logging
import re
from typing import TYPE_CHECKING, Any

from aiqa.models.test_case import Expectation, VerificationResult

if TYPE_CHECKING:
    from playwright.async_api import Page

logger = logging.getLogger(__name__)


def _split_selectors(selector: str) -> list[str]:
    """Split comma-separated CSS/Playwright selectors respecting quotes and brackets.

    Args:
        selector: CSS selector string potentially containing commas.

    Returns:
        List of individual selector candidates.
    """
    parts: list[str] = []
    current: list[str] = []
    in_single = False
    in_double = False
    bracket_depth = 0
    paren_depth = 0

    for char in selector:
        if char == "'" and not in_double:
            in_single = not in_single
            current.append(char)
        elif char == '"' and not in_single:
            in_double = not in_double
            current.append(char)
        elif in_single or in_double:
            current.append(char)
        elif char == "[":
            bracket_depth += 1
            current.append(char)
        elif char == "]":
            bracket_depth = max(0, bracket_depth - 1)
            current.append(char)
        elif char == "(":
            paren_depth += 1
            current.append(char)
        elif char == ")":
            paren_depth = max(0, paren_depth - 1)
            current.append(char)
        elif char == "," and bracket_depth == 0 and paren_depth == 0:
            part = "".join(current).strip()
            if part:
                parts.append(part)
            current = []
        else:
            current.append(char)

    last_part = "".join(current).strip()
    if last_part:
        parts.append(last_part)

    return parts or [selector.strip()]


def _unwrap_page(page: Any) -> Any:
    """Extract underlying Playwright Page if a BrowserSession is passed."""
    if page is None:
        return None
    prop = getattr(type(page), "page", None)
    if isinstance(prop, property):
        try:
            return page.page
        except Exception:
            pass
    return page


class DomVerifier:
    """Verifies test expectations by checking DOM state.

    Supports:
    - Element existence (selector exists / does not exist)
    - Text content (element text matches expected)
    - Element count (number of matching elements or numeric badge text)
    - Attribute value check
    - Page title verification
    """

    async def verify(
        self,
        expectation: Expectation,
        page: Page | Any,
        **kwargs: Any,
    ) -> VerificationResult:
        """Check a DOM expectation against the current page.

        Args:
            expectation: The test expectation to verify.
            page: Playwright Page instance or BrowserSession wrapper.
            **kwargs: Extra parameters passed by callers.

        Returns:
            VerificationResult indicating pass/fail status and observed values.
        """
        # Ensure arguments are in expected order even if inverted by caller
        if not isinstance(expectation, Expectation) and isinstance(page, Expectation):
            expectation, page = page, expectation

        page_obj = _unwrap_page(page)

        try:
            parsed = self._parse_expectation(expectation)
            check_type = parsed["check_type"]

            if check_type == "title":
                return await self._verify_title(expectation, page_obj, parsed)
            elif check_type == "attribute":
                return await self._verify_attribute(expectation, page_obj, parsed)
            elif check_type == "count":
                return await self._verify_count(expectation, page_obj, parsed)
            elif check_type == "existence":
                return await self._verify_existence(expectation, page_obj, parsed)
            else:
                return await self._verify_text(expectation, page_obj, parsed)

        except Exception as exc:
            logger.exception("Error verifying DOM expectation: %s", exc)
            return VerificationResult(
                expectation=expectation,
                passed=False,
                actual_value=None,
                message=f"DOM verification encountered an error: {exc}",
            )

    def _parse_expectation(self, expectation: Expectation) -> dict[str, Any]:
        """Parse expectation metadata and description to determine check strategy.

        Args:
            expectation: Expectation instance.

        Returns:
            Dictionary with parsed check strategy parameters.
        """
        desc = expectation.description.strip()
        sel = expectation.selector.strip() if expectation.selector else None
        val = expectation.value.strip() if expectation.value is not None else None

        # 1. Page Title Check
        if re.search(r"\bpage\s+title\b|\btitle\s+should\b", desc, re.IGNORECASE) or sel == "title":
            title_val = val
            if not title_val:
                m = re.search(
                    r"title\s+should\s+(?:contain|equal|be|match)\s+['\"]?([^'\"]+?)['\"]?\s*$",
                    desc,
                    re.IGNORECASE,
                )
                if m:
                    title_val = m.group(1).strip()
                else:
                    m2 = re.search(r"contain\s+['\"]([^'\"]+)['\"]", desc, re.IGNORECASE)
                    if m2:
                        title_val = m2.group(1).strip()
            return {
                "check_type": "title",
                "selector": sel,
                "target_value": title_val,
                "attribute_name": None,
                "is_negative": False,
            }

        # 2. Attribute Check
        attr_match = re.search(
            r"(?:attribute|attr)\s+['\"]?([a-zA-Z0-9_\-]+)['\"]?(?:\s+of\s+([^\s]+))?\s+should\s+(?:equal|be|contain)\s+['\"]?([^'\"]*?)['\"]?\s*$",
            desc,
            re.IGNORECASE,
        ) or re.search(
            r"should\s+have\s+attribute\s+['\"]?([a-zA-Z0-9_\-]+)['\"]?(?:=['\"]?([^'\"]*?)['\"]?)?",
            desc,
            re.IGNORECASE,
        )
        if attr_match:
            attr_name = attr_match.group(1)
            attr_val = val
            if not attr_val and attr_match.lastindex and attr_match.lastindex >= 2:
                attr_val = attr_match.group(attr_match.lastindex)
            if not sel and attr_match.lastindex and attr_match.lastindex >= 2:
                sel = attr_match.group(2)
            return {
                "check_type": "attribute",
                "selector": sel,
                "target_value": attr_val,
                "attribute_name": attr_name,
                "is_negative": False,
            }

        # Infer selector from description if not provided
        if not sel:
            m_sel = re.search(
                r"(?:[Ee]lement|[Ss]elector)\s+['\"]?([#\.\[][^\s'\"]+)['\"]?\s+should",
                desc,
            )
            if not m_sel:
                m_sel = re.search(
                    r"(?:[Ee]lement|[Ss]elector)\s+['\"]?([a-zA-Z0-9_\-\.#\[\]=:'\"]+)['\"]?\s+should",
                    desc,
                )
            if m_sel:
                sel = m_sel.group(1).strip()

        # 3. Existence Check
        is_neg_exist = bool(
            (val and val.lower() in ("not_exists", "not_exist", "absent", "disappear", "hidden"))
            or re.search(
                r"\bshould\s+not\s+exist\b|\bshould\s+be\s+absent\b|\bshould\s+disappear\b",
                desc,
                re.IGNORECASE,
            )
        )
        is_pos_exist = bool(
            (val and val.lower() in ("exists", "exist", "present", "visible"))
            or re.search(
                r"\bshould\s+exist\b|\bshould\s+be\s+present\b|\bshould\s+be\s+visible\b|\bmust\s+exist\b",
                desc,
                re.IGNORECASE,
            )
        )

        if is_neg_exist or is_pos_exist:
            return {
                "check_type": "existence",
                "selector": sel,
                "target_value": val or ("absent" if is_neg_exist else "exists"),
                "attribute_name": None,
                "is_negative": is_neg_exist,
            }

        # 4. Count Check
        m_count = re.search(
            r"(?:count|quantity)\s+should\s+(?:equal|be)\s+['\"]?(\d+)['\"]?",
            desc,
            re.IGNORECASE,
        )
        if not m_count:
            m_count = re.search(r"should\s+have\s+(\d+)\s+elements?", desc, re.IGNORECASE)

        target_count = None
        if m_count:
            target_count = m_count.group(1)
        elif (
            val is not None
            and val.isdigit()
            and re.search(r"\b(?:count|quantity|items?)\b", desc, re.IGNORECASE)
        ):
            target_count = val

        if target_count is not None:
            if not sel and re.search(r"\bcart\b", desc, re.IGNORECASE):
                sel = (
                    "[data-testid='cart-count'], .cart-count, .cart-badge, "
                    "#cart-count, [data-testid='item-quantity'], input.quantity"
                )
            return {
                "check_type": "count",
                "selector": sel,
                "target_value": target_count,
                "attribute_name": None,
                "is_negative": False,
            }

        # 5. Text Content Check
        text_val = val
        if not text_val:
            m_txt = re.search(
                r"should\s+(?:contain|equal|have\s+text|match|be)\s+['\"]([^'\"]+)['\"]",
                desc,
                re.IGNORECASE,
            )
            if m_txt:
                text_val = m_txt.group(1).strip()

        return {
            "check_type": "text",
            "selector": sel,
            "target_value": text_val,
            "attribute_name": None,
            "is_negative": False,
        }

    async def _verify_title(
        self,
        expectation: Expectation,
        page: Any,
        parsed: dict[str, Any],
    ) -> VerificationResult:
        """Verify page title."""
        target_value = parsed["target_value"]
        actual_title = await self._get_page_title(page)

        if target_value is None:
            passed = bool(actual_title)
            message = f"Page title is '{actual_title}'"
        elif "equal" in expectation.description.lower() or "be" in expectation.description.lower():
            passed = actual_title.strip().lower() == target_value.strip().lower()
            message = (
                f"Page title '{actual_title}' equals expected '{target_value}'"
                if passed
                else f"Page title '{actual_title}' does not equal expected '{target_value}'"
            )
        else:
            passed = target_value.lower() in actual_title.lower()
            message = (
                f"Page title '{actual_title}' contains expected '{target_value}'"
                if passed
                else f"Page title '{actual_title}' does not contain expected '{target_value}'"
            )

        return VerificationResult(
            expectation=expectation,
            passed=passed,
            actual_value=actual_title,
            message=message,
        )

    async def _verify_attribute(
        self,
        expectation: Expectation,
        page: Any,
        parsed: dict[str, Any],
    ) -> VerificationResult:
        """Verify element attribute."""
        selector = parsed["selector"]
        attr_name = parsed["attribute_name"]
        target_value = parsed["target_value"]

        elements = await self._find_elements(page, selector) if selector else []
        if not elements:
            return VerificationResult(
                expectation=expectation,
                passed=False,
                actual_value=None,
                message=(
                    f"No element matching selector '{selector}' found "
                    f"to check attribute '{attr_name}'"
                ),
            )

        actual_attr = await elements[0].get_attribute(attr_name)
        if target_value is None:
            passed = actual_attr is not None
            message = (
                f"Attribute '{attr_name}' is "
                f"{'present' if passed else 'absent'} ({actual_attr})"
            )
        else:
            passed = (actual_attr or "").strip().lower() == target_value.strip().lower()
            message = (
                f"Attribute '{attr_name}' matches expected '{target_value}' "
                f"(actual: '{actual_attr}')"
                if passed
                else f"Attribute '{attr_name}' is '{actual_attr}', expected '{target_value}'"
            )

        return VerificationResult(
            expectation=expectation,
            passed=passed,
            actual_value=actual_attr,
            message=message,
        )

    async def _verify_existence(
        self,
        expectation: Expectation,
        page: Any,
        parsed: dict[str, Any],
    ) -> VerificationResult:
        """Verify element existence or absence."""
        selector = parsed["selector"]
        is_negative = parsed["is_negative"]

        if not selector:
            return VerificationResult(
                expectation=expectation,
                passed=False,
                actual_value=None,
                message=(
                    "Cannot verify existence: no selector provided "
                    "or inferred from description"
                ),
            )

        elements = await self._find_elements(page, selector)
        count = len(elements)

        if is_negative:
            passed = count == 0
            actual_value = "absent" if passed else f"found {count} element(s)"
            message = (
                f"Element matching '{selector}' is absent as expected"
                if passed
                else f"Element matching '{selector}' unexpectedly exists ({count} found)"
            )
        else:
            passed = count > 0
            actual_value = "exists" if passed else "not_found"
            message = (
                f"Element matching '{selector}' exists ({count} found)"
                if passed
                else f"Element matching '{selector}' was not found in DOM"
            )

        return VerificationResult(
            expectation=expectation,
            passed=passed,
            actual_value=actual_value,
            message=message,
        )

    async def _verify_count(
        self,
        expectation: Expectation,
        page: Any,
        parsed: dict[str, Any],
    ) -> VerificationResult:
        """Verify element count or numeric counter badge text."""
        selector = parsed["selector"]
        target_str = parsed["target_value"] or "0"
        target_int = int(target_str)

        if not selector:
            return VerificationResult(
                expectation=expectation,
                passed=False,
                actual_value=None,
                message="Cannot verify count: no selector provided or inferred from description",
            )

        elements = await self._find_elements(page, selector)

        # Strategy 1: Check number of matching elements
        if len(elements) == target_int:
            return VerificationResult(
                expectation=expectation,
                passed=True,
                actual_value=str(len(elements)),
                message=f"Found exactly {len(elements)} element(s) matching '{selector}'",
            )

        # Strategy 2: Check text content or input value of matching element(s)
        # e.g., <span class="cart-count">1</span> or <input value="2" />
        for el in elements:
            text = await self._get_element_text(el)
            clean_text = text.strip()
            if clean_text == str(target_int):
                return VerificationResult(
                    expectation=expectation,
                    passed=True,
                    actual_value=clean_text,
                    message=f"Element '{selector}' text content equals {target_int}",
                )
            num_matches = re.findall(r"\b\d+\b", clean_text)
            if str(target_int) in num_matches:
                return VerificationResult(
                    expectation=expectation,
                    passed=True,
                    actual_value=clean_text,
                    message=(
                        f"Element '{selector}' text content contains count {target_int} "
                        f"(text: '{clean_text}')"
                    ),
                )

        actual_desc = f"{len(elements)} element(s) found"
        if elements:
            first_text = (await self._get_element_text(elements[0])).strip()
            if first_text:
                actual_desc += f", first element text='{first_text}'"

        return VerificationResult(
            expectation=expectation,
            passed=False,
            actual_value=str(len(elements)),
            message=f"Expected count {target_int} for '{selector}', but {actual_desc}",
        )

    async def _verify_text(
        self,
        expectation: Expectation,
        page: Any,
        parsed: dict[str, Any],
    ) -> VerificationResult:
        """Verify element text content."""
        selector = parsed["selector"]
        target_value = parsed["target_value"]
        desc = expectation.description

        if not selector:
            # Fallback to checking full page text
            page_text = ""
            if hasattr(page, "inner_text"):
                try:
                    res = page.inner_text("body")
                    page_text = await res if inspect.isawaitable(res) else str(res)
                except Exception:
                    pass
            if target_value and target_value.lower() in page_text.lower():
                return VerificationResult(
                    expectation=expectation,
                    passed=True,
                    actual_value=target_value,
                    message=f"Page body text contains expected string '{target_value}'",
                )
            return VerificationResult(
                expectation=expectation,
                passed=False,
                actual_value=None,
                message="Cannot verify text: no selector provided or found in page",
            )

        elements = await self._find_elements(page, selector)
        if not elements:
            return VerificationResult(
                expectation=expectation,
                passed=False,
                actual_value=None,
                message=f"No element found matching selector '{selector}' to verify text",
            )

        is_exact = "equal" in desc.lower() or "exact" in desc.lower()
        actual_texts: list[str] = []
        matched = False
        matched_text = ""

        for el in elements:
            text = (await self._get_element_text(el)).strip()
            actual_texts.append(text)
            if target_value is None:
                if text:
                    matched = True
                    matched_text = text
                    break
            elif is_exact:
                if text.lower() == target_value.lower():
                    matched = True
                    matched_text = text
                    break
            else:
                if target_value.lower() in text.lower():
                    matched = True
                    matched_text = text
                    break

        first_actual = actual_texts[0] if actual_texts else ""
        if matched:
            message = (
                f"Element '{selector}' text '{matched_text}' matches expected '{target_value}'"
            )
        else:
            message = (
                f"Element '{selector}' text '{first_actual}' "
                f"did not match expected '{target_value}'"
            )

        return VerificationResult(
            expectation=expectation,
            passed=matched,
            actual_value=matched_text if matched else first_actual,
            message=message,
        )

    async def _find_elements(self, page: Any, selector: str) -> list[Any]:
        """Query elements on the page handling single or comma-separated selectors."""
        if not selector:
            return []

        page_obj = _unwrap_page(page)

        if hasattr(page_obj, "query_selector_all"):
            try:
                res = page_obj.query_selector_all(selector)
                elements = await res if inspect.isawaitable(res) else res
                if elements:
                    return list(elements)

                candidates = _split_selectors(selector)
                if len(candidates) > 1:
                    for cand in candidates:
                        try:
                            res = page_obj.query_selector_all(cand)
                            elements = await res if inspect.isawaitable(res) else res
                            if elements:
                                return list(elements)
                        except Exception:
                            continue
                return []
            except Exception:
                pass

        if hasattr(page_obj, "query_selector"):
            try:
                res = page_obj.query_selector(selector)
                el = await res if inspect.isawaitable(res) else res
                if el is not None:
                    return [el]
            except Exception:
                pass

        return []

    async def _get_element_text(self, el: Any) -> str:
        """Extract text content or input value from an element."""
        if hasattr(el, "text_content"):
            try:
                res = el.text_content()
                text = await res if inspect.isawaitable(res) else res
                if text and str(text).strip():
                    return str(text).strip()
            except Exception:
                pass

        if hasattr(el, "input_value"):
            try:
                res = el.input_value()
                val = await res if inspect.isawaitable(res) else res
                if val and str(val).strip():
                    return str(val).strip()
            except Exception:
                pass

        if hasattr(el, "inner_text"):
            try:
                res = el.inner_text()
                text = await res if inspect.isawaitable(res) else res
                if text and str(text).strip():
                    return str(text).strip()
            except Exception:
                pass

        if hasattr(el, "get_attribute"):
            try:
                res = el.get_attribute("value")
                val = await res if inspect.isawaitable(res) else res
                if val and str(val).strip():
                    return str(val).strip()
            except Exception:
                pass

        return ""

    async def _get_page_title(self, page: Any) -> str:
        """Retrieve document title from page."""
        page_obj = _unwrap_page(page)
        if hasattr(page_obj, "title"):
            try:
                res = page_obj.title()
                title = await res if inspect.isawaitable(res) else res
                return str(title or "").strip()
            except Exception:
                pass
        if hasattr(page_obj, "evaluate"):
            try:
                res = page_obj.evaluate("() => document.title")
                title = await res if inspect.isawaitable(res) else res
                return str(title or "").strip()
            except Exception:
                pass
        return ""


__all__ = ["DomVerifier"]
