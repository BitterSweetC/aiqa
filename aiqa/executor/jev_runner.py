"""Jev Ultrafast runner for executing AIQA test cases."""

from __future__ import annotations

import asyncio
import logging
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from aiqa.executor.browser_session import BrowserSession

logger = logging.getLogger(__name__)

# -----------------------------------------------------------------------------
# Test Case Model Import with Safe Fallback
# -----------------------------------------------------------------------------
try:
    from aiqa.models.test_case import TestCase
except (ImportError, ModuleNotFoundError):
    # Fallback minimal TestCase dataclass when aiqa.models.test_case is not yet
    # imported or defined in the development cycle.
    from dataclasses import dataclass as _dataclass

    @_dataclass
    class TestCase:  # type: ignore[no-redef]
        """Fallback representation of a test case for execution."""

        id: str
        name: str
        start_url: str
        goal: str
        assertions: list[dict[str, Any]] = field(default_factory=list)


# -----------------------------------------------------------------------------
# Jev Ultrafast Import with Simulation Fallback
# -----------------------------------------------------------------------------
# Note: jev-ultrafast is an experimental high-performance browser automation
# library that uses atomic DOM snapshots and indexed action spaces. If it is not
# installed or configured with API keys in the current environment, JevRunner
# automatically activates its simulation fallback. This allows test orchestration,
# verification, and reporting to be developed and tested end-to-end without
# requiring external dependencies or credentials.
try:
    import jev_ultrafast  # type: ignore[import-not-found]
    from jev_ultrafast import Agent as JevAgent  # type: ignore[import-not-found]

    JEV_AVAILABLE = True
except ImportError:
    jev_ultrafast = None  # type: ignore[assignment]
    JevAgent = None  # type: ignore[assignment,misc]
    JEV_AVAILABLE = False


@dataclass
class JevExecutionResult:
    """Result of executing a single test case via Jev or the simulation fallback.

    Attributes:
        success: Whether the execution completed without unhandled exceptions.
        steps: Recorded trace of actions, decisions, and observations.
        final_url: URL of the browser page after execution completes.
        page_html: Captured HTML DOM of the final page for verification.
        screenshot_path: File path to the captured screenshot, or None if unavailable.
        error: Error message string if execution failed, else None.
        duration_seconds: Total execution time in seconds.
    """

    success: bool
    steps: list[dict[str, Any]]
    final_url: str
    page_html: str
    screenshot_path: str | None
    error: str | None
    duration_seconds: float
    state_changed: bool | None = None

    def to_dict(self) -> dict[str, Any]:
        """Convert execution result to dictionary representation."""
        return {
            "success": self.success,
            "steps": self.steps,
            "final_url": self.final_url,
            "page_html": self.page_html,
            "screenshot_path": self.screenshot_path,
            "error": self.error,
            "duration_seconds": self.duration_seconds,
            "state_changed": self.state_changed,
        }


class JevRunner:
    """Executes a test case using jev-ultrafast or simulated fallback.

    Orchestrates the lifecycle of executing a single test case:
    1. Navigates to test.start_url via BrowserSession.
    2. Runs test.goal using jev-ultrafast Agent (or simulated fallback if uninstalled).
    3. Captures the final page DOM state and full-page screenshot.
    4. Returns a comprehensive JevExecutionResult.
    """

    def __init__(
        self,
        screenshots_dir: Path | str | None = None,
        force_mock: bool = False,
    ) -> None:
        """Initialize JevRunner.

        Args:
            screenshots_dir: Directory to store captured screenshots.
                             Defaults to './reports/screenshots'.
            force_mock: If True, forces simulation fallback even if jev-ultrafast
                        is installed (useful for offline unit testing).
        """
        if screenshots_dir is not None:
            self.screenshots_dir = Path(screenshots_dir)
        else:
            self.screenshots_dir = Path("./reports/screenshots")

        self.screenshots_dir.mkdir(parents=True, exist_ok=True)
        self.force_mock = force_mock

        if not JEV_AVAILABLE:
            logger.info(
                "jev-ultrafast is not installed. JevRunner will use the simulation fallback."
            )

    async def run(
        self,
        test: TestCase,
        browser_session: BrowserSession,
    ) -> JevExecutionResult:
        """Run a single test case.

        Execution steps:
        1. Navigate to test.start_url
        2. Execute test.goal with jev-ultrafast Agent (or simulation fallback)
        3. Capture final state + screenshot
        4. Return execution result

        Args:
            test: The TestCase instance containing start_url and goal.
            browser_session: The active BrowserSession to execute against.

        Returns:
            JevExecutionResult detailing outcome, action steps, DOM, and screenshot.
        """
        start_time = time.perf_counter()
        steps: list[dict[str, Any]] = []

        test_id = getattr(test, "id", "TEST")
        test_name = getattr(test, "name", test_id)
        start_url = getattr(test, "start_url", "")
        test_goal = getattr(test, "goal", "")

        # Generate a filesystem-safe identifier for screenshots
        safe_test_id = "".join(
            c if c.isalnum() or c in ("-", "_") else "_" for c in str(test_id)
        )
        timestamp = int(time.time() * 1000)

        # Track whether this method opened the browser session and should close it
        managed_lifecycle = not browser_session.is_active

        try:
            if managed_lifecycle:
                logger.debug("Starting browser session on behalf of JevRunner...")
                await browser_session.start()

            # -----------------------------------------------------------------
            # Step 1: Navigate to test.start_url
            # -----------------------------------------------------------------
            logger.info(
                "Executing test [%s: %s] targeting %s (goal: '%s')",
                test_id,
                test_name,
                start_url,
                test_goal,
            )
            await browser_session.goto(start_url)
            steps.append(
                {
                    "step": 1,
                    "action": "navigate",
                    "url": start_url,
                    "status": "completed",
                    "timestamp": time.time(),
                }
            )

            initial_page_state = await browser_session.get_page_state()
            initial_url = initial_page_state.get("url", start_url)
            initial_html = initial_page_state.get("html") or initial_page_state.get("dom") or ""
            initial_postconditions = await self._capture_postcondition_state(
                test,
                browser_session.page,
            )

            # -----------------------------------------------------------------
            # Step 2: Execute test.goal with jev-ultrafast Agent or fallback
            # -----------------------------------------------------------------
            has_typesafe_key = bool(os.getenv("TYPESAFE_API_KEY"))
            typed_actions = getattr(test, "actions", None)
            if (
                typed_actions is None
                and JEV_AVAILABLE
                and not self.force_mock
                and JevAgent is not None
                and has_typesafe_key
            ):
                execution_steps = await self._run_jev_agent(test, browser_session)
            else:
                # Fallback path: simulates Jev behavior when jev-ultrafast is absent or unconfigured
                execution_steps = await self._run_simulation_fallback(test, browser_session)
            steps.extend(execution_steps)

            if (
                not self._first_required_failure(execution_steps)
                and not self._has_successful_required_action(execution_steps)
            ):
                steps.append(
                    {
                        "step": len(steps) + 1,
                        "action": "execute_actions",
                        "status": "failed",
                        "details": "Execution trace contains no successful required action.",
                        "timestamp": time.time(),
                        "required": True,
                        "error": {
                            "code": "missing_action_outcome",
                            "message": (
                                "Execution trace contains no successful required action."
                            ),
                            "details": {},
                        },
                    }
                )

            # -----------------------------------------------------------------
            # Step 3: Capture final state + screenshot
            # -----------------------------------------------------------------
            page_state = await browser_session.get_page_state()
            final_url = page_state.get("url", start_url)
            page_html = page_state.get("html") or page_state.get("dom") or ""
            final_postconditions = await self._capture_postcondition_state(
                test,
                browser_session.page,
            )
            state_changed = (
                initial_postconditions != final_postconditions
                if initial_postconditions
                else final_url != initial_url or page_html != initial_html
            )

            screenshot_filename = f"{safe_test_id}_{timestamp}.png"
            screenshot_target = self.screenshots_dir / screenshot_filename
            screenshot_path: str | None = None
            try:
                screenshot_path = await browser_session.screenshot(screenshot_target)
            except Exception as ss_exc:  # noqa: BLE001
                logger.warning(
                    "Failed to capture screenshot for test '%s': %s",
                    test_id,
                    ss_exc,
                )

            # -----------------------------------------------------------------
            # Step 4: Return execution result
            # -----------------------------------------------------------------
            failed_action = self._first_required_failure(steps)
            success = failed_action is None
            action_error = self._step_error_message(failed_action) if failed_action else None
            duration = time.perf_counter() - start_time
            logger.info(
                "Completed test [%s: %s] in %.2fs (success=%s)",
                test_id,
                test_name,
                duration,
                success,
            )
            return JevExecutionResult(
                success=success,
                steps=steps,
                final_url=final_url,
                page_html=page_html,
                screenshot_path=screenshot_path,
                error=action_error,
                duration_seconds=round(duration, 3),
                state_changed=state_changed,
            )

        except Exception as exc:
            duration = time.perf_counter() - start_time
            logger.exception("Execution failed for test [%s]", test_id)

            # Attempt to gather current browser state and screenshot on failure
            final_url = start_url
            page_html = ""
            error_screenshot_path: str | None = None

            try:
                if browser_session.is_active:
                    final_url = browser_session.page.url
                    page_html = await browser_session.page.content()
                    err_file = self.screenshots_dir / f"{safe_test_id}_{timestamp}_error.png"
                    error_screenshot_path = await browser_session.screenshot(err_file)
            except Exception as cleanup_exc:  # noqa: BLE001
                logger.debug("Could not capture error state: %s", cleanup_exc)

            return JevExecutionResult(
                success=False,
                steps=steps,
                final_url=final_url,
                page_html=page_html,
                screenshot_path=error_screenshot_path,
                error=str(exc),
                duration_seconds=round(duration, 3),
                state_changed=None,
            )

        finally:
            if managed_lifecycle and browser_session.is_active:
                await browser_session.close()

    @staticmethod
    def _first_required_failure(steps: list[dict[str, Any]]) -> dict[str, Any] | None:
        """Return the first failed required action in an execution trace."""
        return next(
            (
                step
                for step in steps
                if str(step.get("status", "")).lower() in {"failed", "fail", "error"}
                and step.get("required", True)
            ),
            None,
        )

    @staticmethod
    def _has_successful_required_action(steps: list[dict[str, Any]]) -> bool:
        """Return whether the trace proves a supported required action completed."""
        return any(
            step.get("action") in {"click", "fill", "press", "navigate", "wait"}
            and str(step.get("status", "")).lower() in {"completed", "success", "passed"}
            and step.get("required", True)
            for step in steps
        )

    @staticmethod
    def _step_error_message(step: dict[str, Any]) -> str:
        """Extract a readable failure while retaining structured data in the trace."""
        error = step.get("error")
        if isinstance(error, dict) and error.get("message"):
            return str(error["message"])
        return str(step.get("details") or step.get("message") or "Required action failed")

    @staticmethod
    async def _capture_postcondition_state(test: TestCase, page: Any) -> list[dict[str, Any]]:
        """Capture deterministic values used to prove that an interaction had an effect."""
        observations: list[dict[str, Any]] = []
        for index, expectation in enumerate(getattr(test, "expected", None) or []):
            expectation_type = str(getattr(expectation, "type", "")).lower()
            if expectation_type == "url":
                observations.append({"index": index, "type": "url", "value": str(page.url)})
                continue
            if expectation_type != "dom":
                continue

            selector = getattr(expectation, "selector", None)
            if not selector:
                continue
            try:
                locator = page.locator(selector)
                count = await locator.count()
                observation: dict[str, Any] = {
                    "index": index,
                    "type": "dom",
                    "selector": selector,
                    "count": count,
                }
                if count:
                    observation["value"] = await locator.first.evaluate(
                        """element => ({
                            text: element.textContent,
                            value: 'value' in element ? element.value : null,
                            checked: 'checked' in element ? element.checked : null,
                            selectedIndex: 'selectedIndex' in element ? element.selectedIndex : null,
                            hidden: element.hidden,
                            ariaExpanded: element.getAttribute('aria-expanded')
                        })"""
                    )
                observations.append(observation)
            except Exception as exc:  # noqa: BLE001
                observations.append(
                    {
                        "index": index,
                        "type": "dom",
                        "selector": selector,
                        "capture_error": type(exc).__name__,
                    }
                )
        return observations

    async def _run_jev_agent(
        self,
        test: TestCase,
        browser_session: BrowserSession,
    ) -> list[dict[str, Any]]:
        """Execute goal using the real jev-ultrafast Agent.

        Jev is fully self-contained — it opens its own Chrome via CDP (browser-harness)
        and does NOT use Playwright. The Agent is synchronous, so we run it in a worker
        thread. After Jev finishes we navigate the Playwright session to the same final
        URL so the DOM/URL verifiers can inspect the resulting page state.
        """
        logger.info("Running jev-ultrafast Agent for goal: '%s'", test.goal)
        final_url_container: list[str] = [test.start_url]

        def _execute_sync() -> list[dict[str, Any]]:
            trace: list[dict[str, Any]] = []
            if JevAgent is None:
                return trace

            # Jev opens its own browser window via CDP — no context manager needed.
            agent = JevAgent(
                test.start_url,
                test.goal,
                screenshots=True,
            )
            try:
                step_num = 2
                for state in agent.run():
                    if isinstance(state, dict):
                        state_dict = {k: v for k, v in state.items()
                                      if k not in ("page",)}  # skip large DOM blobs
                        state_dict.setdefault("step", step_num)
                        trace.append(state_dict)
                        # Track the latest URL from Jev's page state
                        page = state.get("page") or {}
                        if isinstance(page, dict) and page.get("url"):
                            final_url_container[0] = page["url"]
                    else:
                        trace.append({
                            "step": step_num,
                            "state": str(state),
                            "timestamp": time.time(),
                        })
                    step_num += 1
            finally:
                agent.close()

            return trace

        # Run synchronous Jev in a worker thread (non-blocking)
        agent_steps = await asyncio.to_thread(_execute_sync)

        # After Jev finishes, sync the Playwright page to the final URL so verifiers work
        final_url = final_url_container[0]
        if browser_session.is_active and final_url != test.start_url:
            try:
                await browser_session.goto(final_url)
            except Exception as nav_exc:
                logger.warning("Could not sync Playwright to Jev final URL %s: %s", final_url, nav_exc)

        return agent_steps

    async def _run_simulation_fallback(
        self,
        test: TestCase,
        browser_session: BrowserSession,
    ) -> list[dict[str, Any]]:
        """Autonomous action execution fallback when jev-ultrafast is absent or unconfigured.

        This executor:
        1. Analyzes the live DOM state of the active Playwright page.
        2. Uses ActionDriver (heuristic or LLM-guided) to interact with live elements
           (typing into search boxes, clicking links, clicking buttons, pressing keys).
        3. Returns structured action traces for the test execution report.
        """
        from aiqa.executor.action_driver import ActionDriver

        current_url = browser_session.page.url
        page_title = await browser_session.page.title()

        logger.info(
            "ActionDriver executing goal on '%s' (%s): '%s'",
            page_title,
            current_url,
            test.goal,
        )

        # Record DOM inspection step
        steps: list[dict[str, Any]] = [
            {
                "step": 2,
                "action": "dom_snapshot",
                "status": "completed",
                "details": f"Parsed DOM interactive elements on '{page_title}' ({current_url})",
                "timestamp": time.time(),
            }
        ]

        # Execute live browser actions using ActionDriver
        driver = ActionDriver()
        driver_steps = await driver.execute_goal(
            test.goal,
            browser_session.page,
            actions=getattr(test, "actions", None),
        )
        steps.extend(driver_steps)

        failed_action = self._first_required_failure(driver_steps)
        completion_status = "failed" if failed_action else "completed"
        completion_error = failed_action.get("error") if failed_action else None

        # Record a summary step without masking a failed required action.
        steps.append({
            "step": len(steps) + 2,
            "action": "execute_actions",
            "status": completion_status,
            "goal": test.goal,
            "details": (
                f"Browser action execution failed for goal: {test.goal}"
                if failed_action
                else f"Completed browser actions for goal: {test.goal}"
            ),
            "timestamp": time.time(),
            "required": True,
            **({"error": completion_error} if completion_error else {}),
        })

        return steps
