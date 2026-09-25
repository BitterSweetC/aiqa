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

    async def execute_goal(
        self,
        goal: str,
        page: Any,
        actions: list[BrowserAction] | list[dict[str, Any]] | None = None,
    ) -> list[dict[str, Any]]:
        """Execute a validated plan or migrate a legacy goal into an action trace."""
        logger.info("ActionDriver executing goal: '%s'", goal)

        if actions is not None:
            return await self._execute_action_plan(actions, page)

        # If LLM API key is present, try LLM-driven planning for complex goals
        if self.api_key:
            try:
                llm_steps = await self._execute_with_llm(goal, page)
                if llm_steps:
                    return llm_steps
            except Exception as exc:
                logger.warning("LLM action planning failed (%s). Falling back to heuristic driver.", exc)

        # Fallback to intelligent heuristic action driver
        return await self._execute_with_heuristics(goal, page)

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
    ) -> list[dict[str, Any]]:
        """Validate and execute an inspectable browser action plan in order."""
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

        outcomes: list[dict[str, Any]] = []
        for index, action in enumerate(plan, start=1):
            try:
                if action.action == "click":
                    await page.locator(action.selector).first.click(timeout=8000)
                elif action.action == "fill":
                    await page.locator(action.selector).first.fill(action.value)
                elif action.action == "press":
                    if action.selector:
                        await page.locator(action.selector).first.press(action.key)
                    else:
                        await page.keyboard.press(action.key)
                elif action.action == "navigate":
                    await page.goto(action.url, timeout=15_000)
                elif action.action == "wait":
                    await page.wait_for_timeout(action.timeout_ms)

                details = action.description or f"Executed {action.action} action"
                outcome = ActionOutcome(
                    step=index,
                    action=action.action,
                    status="completed",
                    details=details,
                    timestamp=time.time(),
                    required=action.required,
                )
                outcomes.append(outcome.model_dump(exclude_none=True))
            except Exception as exc:  # noqa: BLE001
                message = action.description or f"Failed to execute {action.action} action"
                outcomes.append(
                    self._failure_step(
                        action.action,
                        "action_execution_failed",
                        f"{message}: {exc}",
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
                    except Exception:
                        continue

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
                    except Exception:
                        pass
                except Exception as exc:
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
                        except Exception:
                            pass
                        break
                except Exception:
                    continue

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
                        except Exception:
                            pass
                        break
                except Exception:
                    continue

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
    ) -> list[dict[str, Any]]:
        """Use LLM to inspect page interactive elements and execute actions dynamically."""
        import openai

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

        raw_output = response.choices[0].message.content or ""
        # Strip markdown fences
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
        return await self._execute_action_plan(actions, page)
