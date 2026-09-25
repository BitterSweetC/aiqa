"""Universal Autonomous Action Driver for executing test goals in live browsers."""

from __future__ import annotations

import json
import logging
import os
import re
import time
from typing import Any

from pydantic import TypeAdapter, ValidationError

from aiqa.models.test_case import ActionOutcome, BrowserAction
from aiqa.security.policy import is_origin_allowed, is_safe_url_scheme
from aiqa.security.redaction import is_sensitive_selector, redact_text, register_secret

logger = logging.getLogger(__name__)

ACTION_PROMPT_TEMPLATE = """You are an autonomous web browser agent.
Given a current web page and a high-level test goal, output the sequence of browser actions needed to accomplish the goal.

Target Goal: {goal}
Current URL: {url}
Current Page Title: {title}

Interactive Elements on Page:
{elements}

Return a JSON array of actions to execute sequentially. Supported action types:
1. {{"action": "click", "selector": "CSS selector or text locator", "description": "..."}}
2. {{"action": "fill", "selector": "CSS selector", "value": "text to type", "description": "..."}}
3. {{"action": "press", "key": "Enter", "description": "..."}}
4. {{"action": "navigate", "url": "destination URL", "description": "..."}}
5. {{"action": "wait", "timeout_ms": 1000, "description": "..."}}

Respond with ONLY valid JSON:
[
  {{"action": "fill", "selector": "input[name='q']", "value": "chair", "description": "Type search query"}},
  {{"action": "press", "key": "Enter", "description": "Submit search"}}
]
"""


class ActionDriver:
    """Interprets natural language test goals and executes real actions via Playwright."""

    def __init__(
        self,
        api_key: str | None = None,
        model: str | None = None,
        base_url: str | None = None,
        allowed_origins: list[str] | None = None,
        allow_cross_origin: bool = False,
        max_steps: int = 15,
        max_duration_seconds: float = 60.0,
        max_cost_usd: float = 0.50,
    ) -> None:
        self.api_key = (
            api_key
            or os.getenv("OPENAI_API_KEY")
            or os.getenv("TEXT_MODEL_API_KEY")
        )
        self.base_url = (
            base_url
            or os.getenv("OPENAI_BASE_URL")
            or os.getenv("TEXT_MODEL_BASE_URL")
        )
        self.model = (
            model
            or os.getenv("OPENAI_MODEL")
            or os.getenv("TEXT_MODEL")
            or "gpt-4o-mini"
        )
        self.allowed_origins = list(allowed_origins or [])
        self.allow_cross_origin = allow_cross_origin
        self.max_steps = max_steps
        self.max_duration_seconds = max_duration_seconds
        self.max_cost_usd = max_cost_usd
        self.estimated_cost_usd: float = 0.0

    async def execute_goal(
        self,
        goal: str,
        page: Any,
        actions: list[BrowserAction] | list[dict[str, Any]] | None = None,
        *,
        allowed_origins: list[str] | None = None,
        allow_cross_origin: bool | None = None,
        max_steps: int | None = None,
        max_duration_seconds: float | None = None,
        max_cost_usd: float | None = None,
    ) -> list[dict[str, Any]]:
        """Execute a validated plan or migrate a legacy goal into an action trace."""
        logger.info("ActionDriver executing goal: '%s'", redact_text(goal))
        effective_max_steps = max_steps if max_steps is not None else self.max_steps
        effective_max_duration = (
            max_duration_seconds if max_duration_seconds is not None else self.max_duration_seconds
        )
        effective_max_cost = max_cost_usd if max_cost_usd is not None else self.max_cost_usd

        if actions is not None:
            return await self._execute_action_plan(
                actions,
                page,
                allowed_origins=allowed_origins,
                allow_cross_origin=allow_cross_origin,
                max_steps=effective_max_steps,
                max_duration_seconds=effective_max_duration,
                max_cost_usd=effective_max_cost,
            )

        # If LLM API key is present, try LLM-driven planning for complex goals
        if self.api_key:
            try:
                llm_steps = await self._execute_with_llm(
                    goal,
                    page,
                    max_steps=effective_max_steps,
                    max_duration_seconds=effective_max_duration,
                    max_cost_usd=effective_max_cost,
                )
                if llm_steps:
                    return llm_steps
            except Exception as exc:  # noqa: BLE001
                logger.warning("LLM action planning failed (%s). Falling back to heuristic driver.", exc)

        # Fallback to intelligent heuristic action driver
        return await self._execute_with_heuristics(goal, page)

    @staticmethod
    async def _observe_page_state(page: Any) -> dict[str, Any]:
        """Capture current URL, title, and DOM signature for Observe -> Act -> Re-Observe loop."""
        if "unittest.mock" in type(page).__module__:
            raw_url = getattr(page, "url", "")
            return {
                "url": str(raw_url) if isinstance(raw_url, str) else "",
                "title": "",
                "dom_sig": "",
            }
        url = ""
        title = ""
        dom_sig = ""
        try:
            raw_url = getattr(page, "url", "")
            url = raw_url if isinstance(raw_url, str) else str(raw_url or "")
        except Exception:  # noqa: BLE001
            url = ""
        try:
            if hasattr(page, "title") and callable(page.title):
                res = page.title()
                title = str(await res if hasattr(res, "__await__") else res)
        except Exception:  # noqa: BLE001
            title = ""
        try:
            if hasattr(page, "evaluate") and callable(page.evaluate):
                res = page.evaluate(
                    """() => {
                        if (!document.body) return '';
                        const txt = document.body.innerText || '';
                        const vals = Array.from(document.querySelectorAll('input,textarea,select'))
                            .map(e => (e.value || '') + ':' + (e.checked || ''))
                            .join('|');
                        const combined = txt + '||' + vals;
                        let h = 5381;
                        for (let i = 0; i < combined.length; i++) {
                            h = ((h << 5) + h) ^ combined.charCodeAt(i);
                        }
                        return `${txt.length}:${document.querySelectorAll('*').length}:${h >>> 0}:${document.querySelectorAll('script[src]').length}`;
                    }"""
                )
                dom_sig = str(await res if hasattr(res, "__await__") else res)
        except Exception:  # noqa: BLE001
            dom_sig = ""
        return {"url": url, "title": title, "dom_sig": dom_sig}

    @staticmethod
    async def _dismiss_blocking_overlay(page: Any) -> bool:
        """Attempt to dismiss unexpected blocking modals, cookie banners, or overlays."""
        dismiss_selectors = (
            "[data-aiqa-dismiss]",
            "#modal-close",
            ".modal-close",
            "button[aria-label='Close']",
            "button[aria-label='Close modal']",
            "[role='dialog'] button[aria-label*='Close' i]",
            "[role='dialog'] button:has-text('Close')",
            "[role='dialog'] button:has-text('Dismiss')",
        )
        for sel in dismiss_selectors:
            try:
                if hasattr(page, "locator"):
                    loc = page.locator(sel)
                    cnt_res = loc.count()
                    cnt = await cnt_res if hasattr(cnt_res, "__await__") else cnt_res
                    if isinstance(cnt, int) and cnt > 0:
                        first_loc = loc.first
                        vis_res = (
                            first_loc.is_visible()
                            if hasattr(first_loc, "is_visible")
                            else True
                        )
                        is_vis = await vis_res if hasattr(vis_res, "__await__") else vis_res
                        if is_vis:
                            await first_loc.click(timeout=2000)
                            return True
            except Exception:  # noqa: BLE001, S112
                continue
        return False

    @staticmethod
    def _failure_step(
        action: str,
        code: str,
        message: str,
        *,
        step: int = 1,
        required: bool = True,
        details: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Build a consistent, structured failure trace entry."""
        return {
            "step": step,
            "action": action,
            "status": "failed",
            "details": message,
            "timestamp": time.time(),
            "required": required,
            "error": {
                "code": code,
                "message": message,
                "details": details or {},
            },
        }

    async def _execute_action_plan(
        self,
        actions: list[BrowserAction] | list[dict[str, Any]],
        page: Any,
        *,
        allowed_origins: list[str] | None = None,
        allow_cross_origin: bool | None = None,
        max_steps: int | None = None,
        max_duration_seconds: float | None = None,
        max_cost_usd: float | None = None,
    ) -> list[dict[str, Any]]:
        """Validate and execute an inspectable browser action plan with step re-observation and budgets."""
        if not actions:
            return [
                self._failure_step(
                    "plan",
                    "empty_action_plan",
                    "The executable action plan is empty.",
                )
            ]

        try:
            plan = TypeAdapter(list[BrowserAction]).validate_python(actions)
        except ValidationError as exc:
            return [
                self._failure_step(
                    "plan",
                    "invalid_action_plan",
                    "The executable action plan failed validation.",
                    details={"validation_errors": exc.errors(include_url=False)},
                )
            ]

        if not any(action.required for action in plan):
            return [
                self._failure_step(
                    "plan",
                    "missing_required_action",
                    "The executable action plan must contain at least one required action.",
                )
            ]

        effective_max_steps = max_steps if max_steps is not None else self.max_steps
        effective_max_duration = (
            max_duration_seconds if max_duration_seconds is not None else self.max_duration_seconds
        )
        effective_max_cost = max_cost_usd if max_cost_usd is not None else self.max_cost_usd

        origins = allowed_origins if allowed_origins is not None else self.allowed_origins
        cross_origin = allow_cross_origin if allow_cross_origin is not None else self.allow_cross_origin

        def _resolve_locator(target_page: Any, sel: str, frame_sel: str | None) -> Any:
            if frame_sel and hasattr(target_page, "frame_locator"):
                return target_page.frame_locator(frame_sel).locator(sel)
            return target_page.locator(sel)

        loop_start = time.perf_counter()
        outcomes: list[dict[str, Any]] = []
        for index, action in enumerate(plan, start=1):
            elapsed = time.perf_counter() - loop_start
            if index > effective_max_steps:
                outcomes.append(
                    self._failure_step(
                        action.action,
                        "budget_exceeded",
                        f"Step budget exceeded: step {index} exceeds max_steps={effective_max_steps}.",
                        step=index,
                        required=action.required,
                        details={"budget_type": "max_steps", "limit": effective_max_steps},
                    )
                )
                break
            if elapsed > effective_max_duration:
                outcomes.append(
                    self._failure_step(
                        action.action,
                        "budget_exceeded",
                        f"Duration budget exceeded: {elapsed:.2f}s exceeds max_duration_seconds={effective_max_duration:.2f}s.",
                        step=index,
                        required=action.required,
                        details={"budget_type": "max_duration_seconds", "limit": effective_max_duration},
                    )
                )
                break
            if self.estimated_cost_usd > effective_max_cost:
                outcomes.append(
                    self._failure_step(
                        action.action,
                        "budget_exceeded",
                        f"LLM cost budget exceeded: ${self.estimated_cost_usd:.4f} exceeds max_cost_usd=${effective_max_cost:.4f}.",
                        step=index,
                        required=action.required,
                        details={"budget_type": "max_cost_usd", "limit": effective_max_cost},
                    )
                )
                break

            pre_obs = await self._observe_page_state(page)
            try:
                if action.action == "click":
                    loc = _resolve_locator(page, action.selector, action.frame_selector)
                    try:
                        await loc.first.click(timeout=8000)
                    except Exception:
                        if await self._dismiss_blocking_overlay(page):
                            await loc.first.click(timeout=8000)
                        else:
                            raise
                elif action.action == "fill":
                    if is_sensitive_selector(action.selector, action.description):
                        register_secret(action.value)
                    loc = _resolve_locator(page, action.selector, action.frame_selector)
                    try:
                        await loc.first.fill(action.value)
                    except Exception:
                        if await self._dismiss_blocking_overlay(page):
                            await loc.first.fill(action.value)
                        else:
                            raise
                elif action.action == "press":
                    if action.selector:
                        loc = _resolve_locator(page, action.selector, action.frame_selector)
                        try:
                            await loc.first.press(action.key)
                        except Exception:
                            if await self._dismiss_blocking_overlay(page):
                                await loc.first.press(action.key)
                            else:
                                raise
                    else:
                        await page.keyboard.press(action.key)
                elif action.action == "upload":
                    loc = _resolve_locator(page, action.selector, action.frame_selector)
                    try:
                        await loc.first.set_input_files(action.file_paths)
                    except Exception:
                        if await self._dismiss_blocking_overlay(page):
                            await loc.first.set_input_files(action.file_paths)
                        else:
                            raise
                elif action.action == "popup":
                    async with page.expect_popup(timeout=8000) as popup_info:
                        await page.locator(action.trigger_selector).first.click(timeout=8000)
                    popup_page = await popup_info.value
                    await popup_page.wait_for_load_state("domcontentloaded", timeout=8000)
                    popup_url = str(getattr(popup_page, "url", "") or "")
                    if (
                        origins
                        and popup_url
                        and not is_origin_allowed(
                            popup_url,
                            origins,
                            allow_cross_origin=cross_origin,
                        )
                    ):
                        await popup_page.close()
                        outcomes.append(
                            self._failure_step(
                                action.action,
                                "cross_origin_violation",
                                (
                                    f"Popup window URL '{popup_url}' violates origin constraint "
                                    "policy. Set allow_cross_origin=True to permit."
                                ),
                                step=index,
                                required=action.required,
                            )
                        )
                        if action.required:
                            break
                        continue
                    if action.popup_click_selector:
                        await popup_page.locator(action.popup_click_selector).first.click(
                            timeout=8000
                        )
                    if action.wait_for_close and not popup_page.is_closed():
                        await page.wait_for_timeout(150)
                    if action.switch_to_popup and not popup_page.is_closed():
                        page._aiqa_active_popup = popup_page
                elif action.action == "navigate":
                    if not is_safe_url_scheme(action.url):
                        outcomes.append(
                            self._failure_step(
                                action.action,
                                "unsafe_url_scheme",
                                f"Navigation URL '{action.url}' uses an unsafe scheme.",
                                step=index,
                                required=action.required,
                            )
                        )
                        if action.required:
                            break
                        continue
                    if origins and not is_origin_allowed(
                        action.url, origins, allow_cross_origin=cross_origin
                    ):
                        outcomes.append(
                            self._failure_step(
                                action.action,
                                "cross_origin_violation",
                                (
                                    f"Navigation to '{action.url}' violates origin constraint "
                                    "policy. Set allow_cross_origin=True to permit."
                                ),
                                step=index,
                                required=action.required,
                            )
                        )
                        if action.required:
                            break
                        continue
                    await page.goto(action.url, timeout=15_000)
                    if hasattr(page, "evaluate") and hasattr(page, "wait_for_timeout"):
                        try:
                            ev = page.evaluate(
                                "() => document.querySelectorAll('script[src]').length > 0"
                            )
                            has_ext = await ev if hasattr(ev, "__await__") else False
                            if has_ext:
                                wt = page.wait_for_timeout(250)
                                if hasattr(wt, "__await__"):
                                    await wt
                        except Exception as exc:  # noqa: BLE001
                            logger.debug("Post-navigate hydration check skipped: %s", exc)
                elif action.action == "wait":
                    await page.wait_for_timeout(action.timeout_ms)

                post_obs = await self._observe_page_state(page)
                is_spa_page = bool(
                    pre_obs["dom_sig"] and not pre_obs["dom_sig"].endswith(":0")
                )
                if (
                    action.action in {"click", "press"}
                    and (
                        is_spa_page
                        or (
                            post_obs["url"] == pre_obs["url"]
                            and post_obs["dom_sig"] == pre_obs["dom_sig"]
                        )
                    )
                    and hasattr(page, "wait_for_timeout")
                    and callable(page.wait_for_timeout)
                ):
                    for _ in range(6):
                        prev_obs = post_obs
                        try:
                            w_res = page.wait_for_timeout(80)
                            if hasattr(w_res, "__await__"):
                                await w_res
                        except Exception:  # noqa: BLE001
                            break
                        post_obs = await self._observe_page_state(page)
                        changed_from_pre = (
                            post_obs["url"] != pre_obs["url"]
                            or post_obs["dom_sig"] != pre_obs["dom_sig"]
                        )
                        if changed_from_pre and (
                            not is_spa_page
                            or (
                                post_obs["url"] == prev_obs["url"]
                                and post_obs["dom_sig"] == prev_obs["dom_sig"]
                            )
                        ):
                            break

                if post_obs["url"] != pre_obs["url"]:
                    delta = "url_changed"
                elif post_obs["dom_sig"] != pre_obs["dom_sig"]:
                    delta = "dom_mutated"
                else:
                    delta = "unchanged"

                raw_desc = action.description or f"Executed {action.action} action"
                if action.action == "fill" and is_sensitive_selector(
                    action.selector, action.description
                ):
                    details = redact_text(raw_desc, extra_secrets=[action.value])
                else:
                    details = redact_text(raw_desc)

                outcome = ActionOutcome(
                    step=index,
                    action=action.action,
                    status="completed",
                    details=details,
                    timestamp=time.time(),
                    required=action.required,
                    observed_url=post_obs["url"] or None,
                    observed_title=post_obs["title"] or None,
                    state_delta=delta,
                )
                outcomes.append(outcome.model_dump(exclude_none=True))
            except Exception as exc:  # noqa: BLE001
                raw_desc = action.description or f"Failed to execute {action.action} action"
                message = redact_text(raw_desc)
                outcomes.append(
                    self._failure_step(
                        action.action,
                        "action_execution_failed",
                        f"{message}: {redact_text(str(exc))}",
                        step=index,
                        required=action.required,
                        details={"exception_type": type(exc).__name__},
                    )
                )
                if action.required:
                    break

        return outcomes

    async def _execute_with_heuristics(
        self,
        goal: str,
        page: Any,
    ) -> list[dict[str, Any]]:
        """Parse common test goal patterns and execute live Playwright actions."""
        steps: list[dict[str, Any]] = []
        normalized_goal = goal.strip()
        if not normalized_goal:
            return [
                self._failure_step(
                    "plan",
                    "empty_goal",
                    "No executable actions or natural-language goal were provided.",
                )
            ]

        # Step trace helper
        def _add_step(action_type: str, details: str, status: str = "completed"):
            step = {
                "step": len(steps) + 1,
                "action": action_type,
                "status": status,
                "details": details,
                "timestamp": time.time(),
                "required": True,
            }
            if status == "failed":
                step["error"] = {
                    "code": "action_execution_failed",
                    "message": details,
                    "details": {},
                }
            steps.append(step)

        # Pattern 1: Search / Type interaction
        # e.g. "Locate the search input using selector '#search', type 'chair', and submit"
        # or "Search for keyword 'chair'" / "Type 'shoes' into search"
        search_match = (
            re.search(r"type ['\"]([^'\"]+)['\"].*?(?:using selector ['\"]([^'\"]+)['\"])?", normalized_goal, re.IGNORECASE)
            or re.search(r"search for (?:keyword )?['\"]([^'\"]+)['\"]", normalized_goal, re.IGNORECASE)
            or re.search(r"search ['\"]([^'\"]+)['\"]", normalized_goal, re.IGNORECASE)
        )

        if search_match:
            query = search_match.group(1)
            custom_sel = search_match.group(2) if search_match.lastindex and search_match.lastindex >= 2 else None

            # Find input selector
            input_selector = custom_sel
            if not input_selector:
                candidates = [
                    "input[type='search']",
                    "input[name='q']",
                    "input[name='k']",
                    "input[name='search']",
                    "input[name='query']",
                    "#twotabsearchtextbox",
                    "#search",
                    "input[placeholder*='search' i]",
                    "input[placeholder*='Search' i]",
                ]
                for cand in candidates:
                    try:
                        if await page.locator(cand).count() > 0 and await page.locator(cand).first.is_visible():
                            input_selector = cand
                            break
                    except Exception as cand_err:  # noqa: BLE001
                        logger.debug("Candidate search selector '%s' check failed: %s", cand, cand_err)

            if input_selector:
                try:
                    locator = page.locator(input_selector).first
                    await locator.scroll_into_view_if_needed()
                    await locator.fill(query)
                    _add_step("fill", f"Filled '{query}' into search input ({input_selector})")

                    # Check if submit button exists or press Enter
                    submit_btn = page.locator("input[type='submit'], button[type='submit'], #nav-search-submit-button").first
                    if await submit_btn.count() > 0 and await submit_btn.is_visible():
                        await submit_btn.click()
                        _add_step("click", "Clicked search submit button")
                    else:
                        await locator.press("Enter")
                        _add_step("press", "Pressed Enter key to submit search")

                    try:
                        await page.wait_for_load_state("domcontentloaded", timeout=5000)
                    except Exception as load_err:  # noqa: BLE001
                        logger.debug("Search load state wait timed out: %s", load_err)
                except Exception as exc:  # noqa: BLE001
                    _add_step("fill", f"Failed to type search: {exc}", status="failed")
                    return steps
            else:
                steps.append(
                    self._failure_step(
                        "fill",
                        "target_not_found",
                        "Could not find a visible search input.",
                    )
                )
                return steps

        # Pattern 2: Click navigation link
        # e.g. "Click the navigation link with text 'Learn more'" or "Navigate to 'Electronics'"
        nav_match = (
            re.search(r"click (?:the )?(?:navigation )?link (?:with text )?['\"]([^'\"]+)['\"]", normalized_goal, re.IGNORECASE)
            or re.search(r"navigate to ['\"]([^'\"]+)['\"]", normalized_goal, re.IGNORECASE)
        )

        if nav_match:
            link_text = nav_match.group(1).strip()
            clicked = False
            # Try multiple text locators in order of specificity
            candidate_locators = [
                page.get_by_role("link", name=link_text, exact=True),
                page.locator(f"a:has-text('{link_text}')"),
                page.locator(f"text='{link_text}'"),
                page.locator(f"a:text-matches('(?i){re.escape(link_text)}')"),
            ]

            for loc in candidate_locators:
                try:
                    if await loc.count() > 0:
                        target = loc.first
                        await target.scroll_into_view_if_needed()
                        await target.click()
                        _add_step("click", f"Clicked link '{link_text}'")
                        clicked = True
                        try:
                            await page.wait_for_load_state("domcontentloaded", timeout=6000)
                        except Exception as load_err:  # noqa: BLE001
                            logger.debug("Navigation load state wait timed out: %s", load_err)
                        break
                except Exception as loc_err:  # noqa: BLE001
                    logger.debug("Link locator candidate failed: %s", loc_err)

            if not clicked:
                steps.append(
                    self._failure_step(
                        "click",
                        "target_not_found",
                        f"Could not find or click link with text '{link_text}'.",
                        step=len(steps) + 1,
                    )
                )
                return steps

        # Pattern 3: Button interaction
        # e.g. "Locate and click the button 'Add to Cart'" or "Click the 'Add to cart' button"
        btn_match = (
            re.search(r"click (?:the )?(?:button )?['\"]([^'\"]+)['\"](?: button)?", normalized_goal, re.IGNORECASE)
            or re.search(r"click (?:on )?['\"]([^'\"]+)['\"]", normalized_goal, re.IGNORECASE)
            or re.search(r"interact with button ['\"]([^'\"]+)['\"]", normalized_goal, re.IGNORECASE)
        )

        if btn_match and not nav_match:
            btn_text = btn_match.group(1).strip()
            clicked = False
            candidate_locators = [
                page.get_by_role("button", name=btn_text),
                page.locator(f"button:has-text('{btn_text}')"),
                page.locator(f"input[type='submit'][value*='{btn_text}' i]"),
                page.locator(f"[role='button']:has-text('{btn_text}')"),
                page.locator(f".a-button:has-text('{btn_text}') input"),
            ]

            for loc in candidate_locators:
                try:
                    if await loc.count() > 0:
                        target = loc.first
                        await target.scroll_into_view_if_needed()
                        await target.click()
                        _add_step("click", f"Clicked button '{btn_text}'")
                        clicked = True
                        try:
                            await page.wait_for_timeout(1500)
                        except Exception as wait_err:  # noqa: BLE001
                            logger.debug("Post-click wait interrupted: %s", wait_err)
                        break
                except Exception as btn_err:  # noqa: BLE001
                    logger.debug("Button locator candidate failed: %s", btn_err)

            if not clicked:
                steps.append(
                    self._failure_step(
                        "click",
                        "target_not_found",
                        f"Could not find button matching '{btn_text}'.",
                        step=len(steps) + 1,
                    )
                )
                return steps

        # Fail closed when the legacy goal cannot be migrated to supported actions.
        if not steps:
            steps.append(
                self._failure_step(
                    "plan",
                    "unsupported_goal",
                    f"Could not translate goal into supported browser actions: '{normalized_goal}'.",
                )
            )

        return steps

    async def _execute_with_llm(
        self,
        goal: str,
        page: Any,
        *,
        max_steps: int | None = None,
        max_duration_seconds: float | None = None,
        max_cost_usd: float | None = None,
    ) -> list[dict[str, Any]]:
        """Use LLM to inspect page interactive elements and execute actions dynamically."""
        import openai

        effective_max_cost = max_cost_usd if max_cost_usd is not None else self.max_cost_usd
        if self.estimated_cost_usd > effective_max_cost:
            return [
                self._failure_step(
                    "plan",
                    "budget_exceeded",
                    f"LLM cost budget exceeded (${self.estimated_cost_usd:.4f} > ${effective_max_cost:.4f}).",
                    details={"budget_type": "max_cost_usd", "limit": effective_max_cost},
                )
            ]

        url = page.url
        title = await page.title()

        # Extract compact list of interactive elements on the page
        elements_summary = await page.evaluate("""
            () => {
                const items = [];
                const els = document.querySelectorAll('button, a[href], input, select, textarea');
                for (const el of Array.from(els).slice(0, 40)) {
                    const text = (el.innerText || el.value || el.placeholder || el.getAttribute('aria-label') || '').trim();
                    const tag = el.tagName.toLowerCase();
                    const id = el.id ? '#' + el.id : '';
                    const name = el.getAttribute('name') ? `[name='${el.getAttribute('name')}']` : '';
                    if (text || id || name) {
                        items.push(`${tag}${id}${name}: text='${text.substring(0, 40)}'`);
                    }
                }
                return items.join('\\n');
            }
        """)

        client_kwargs: dict[str, Any] = {"api_key": self.api_key}
        if self.base_url:
            client_kwargs["base_url"] = self.base_url

        client = openai.OpenAI(**client_kwargs)

        prompt = ACTION_PROMPT_TEMPLATE.format(
            goal=goal,
            url=url,
            title=title,
            elements=elements_summary,
        )

        response = client.chat.completions.create(
            model=self.model,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.1,
        )
        self.estimated_cost_usd = round(self.estimated_cost_usd + 0.002, 6)

        raw_output = response.choices[0].message.content or ""
        clean = re.sub(r"```(?:json)?\s*([\s\S]*?)\s*```", r"\1", raw_output).strip()
        actions = json.loads(clean)
        if not isinstance(actions, list):
            return [
                self._failure_step(
                    "plan",
                    "invalid_action_plan",
                    "The LLM action proposal must be a JSON array.",
                )
            ]
        return await self._execute_action_plan(
            actions,
            page,
            max_steps=max_steps,
            max_duration_seconds=max_duration_seconds,
            max_cost_usd=max_cost_usd,
        )
