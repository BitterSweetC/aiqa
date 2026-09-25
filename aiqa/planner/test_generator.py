"""LLM-powered test suite generator for autonomous web QA."""

from __future__ import annotations

import json
import logging
import os
import re
from pathlib import Path
from typing import Any

from aiqa.models.test_case import Expectation, TestCase, TestSuite
from aiqa.planner.site_inspector import InspectedPage

logger = logging.getLogger(__name__)

PLANNER_SYSTEM_PROMPT = """You are an expert QA Automation Engineer.
Your task is to analyze the structure of a web application and generate a comprehensive, runnable automated test suite adhering strictly to the JSON schema provided.

Each test case MUST have:
- "id": A unique identifier, e.g. "SMOKE-001", "SEARCH-001", "CART-001", "NAV-001".
- "name": Concise title of the test.
- "start_url": The exact starting URL for this test.
- "preconditions": List of natural language requirements (or empty list []).
- "goal": Clear, step-by-step natural language instructions for the browser agent (e.g. "Find the search input '#twotabsearchtextbox', type 'office chair', press Enter, and wait for results.").
- "actions": Non-empty validated executable plan. Supported shapes are:
  - {"action": "click", "selector": "#save", "description": "Click Save"}
  - {"action": "fill", "selector": "#query", "value": "chair"}
  - {"action": "press", "selector": "#query", "key": "Enter"}
  - {"action": "navigate", "url": "https://example.com/products"}
  - {"action": "wait", "timeout_ms": 500} (maximum 10000)
- "expected": List of assertions to verify after execution. Each assertion has:
  - "type": "dom", "url", or "semantic"
  - "description": What should be true (e.g. "Search result cards should appear", "URL contains 'search'")
  - "selector": CSS selector for dom assertions (optional)
  - "value": Expected text or value (optional)
- "cleanup": List of cleanup steps or empty [].
- "tags": Relevant tags like ["smoke", "search", "p0"].
- "timeout": Integer timeout in seconds (default 60).

Return ONLY valid JSON matching this schema:
{
  "name": "Suite Name",
  "base_url": "https://example.com",
  "tests": [ ... ]
}
Do not include any markdown backticks or commentary outside the JSON.
"""


class TestPlanner:
    """Generates structured TestSuite from inspected page structure and user goals."""

    __test__ = False

    def __init__(
        self,
        api_key: str | None = None,
        base_url: str | None = None,
        model: str | None = None,
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

    def generate_suite(
        self,
        inspected: InspectedPage,
        goal: str | None = None,
        max_tests: int = 5,
    ) -> TestSuite:
        """Generate a TestSuite using an LLM, or fallback to heuristic generation."""
        if self.api_key:
            try:
                logger.info("Generating test suite using LLM (%s)...", self.model)
                return self._generate_with_llm(inspected, goal, max_tests)
            except Exception as exc:
                logger.warning(
                    "LLM test generation failed (%s). Falling back to heuristic generator.",
                    exc,
                )

        logger.info("Generating test suite using heuristic generator...")
        return self._generate_heuristics(inspected, goal, max_tests)

    def _generate_with_llm(
        self,
        inspected: InspectedPage,
        goal: str | None,
        max_tests: int,
    ) -> TestSuite:
        """Call LLM API to produce structured test suite."""
        import openai

        client_kwargs: dict[str, Any] = {"api_key": self.api_key}
        if self.base_url:
            client_kwargs["base_url"] = self.base_url

        client = openai.OpenAI(**client_kwargs)

        user_content_lines = [
            f"Target URL: {inspected.url}",
            f"Page Title: {inspected.title}",
            "",
            "Inspected Page Structure:",
            inspected.to_prompt_context(),
            "",
            f"Maximum Tests to Generate: {max_tests}",
        ]
        if goal:
            user_content_lines.append(f"Testing Goal / Focus: {goal}")
        else:
            user_content_lines.append(
                "Testing Goal: Create core smoke, navigation, search, and primary interaction tests."
            )

        response = client.chat.completions.create(
            model=self.model,
            messages=[
                {"role": "system", "content": PLANNER_SYSTEM_PROMPT},
                {"role": "user", "content": "\n".join(user_content_lines)},
            ],
            temperature=0.2,
        )

        raw_output = response.choices[0].message.content or ""
        # Clean any markdown code fences if present
        clean_json = self._extract_json(raw_output)
        data = json.loads(clean_json)

        # Validate with Pydantic
        suite = TestSuite.model_validate(data)
        invalid_ids = [test.id for test in suite.tests if not self._is_runnable_generated_test(test)]
        if invalid_ids:
            raise ValueError(
                "Generated tests lack a supported action plan or meaningful deterministic "
                f"postcondition: {', '.join(invalid_ids)}"
            )
        return suite

    def _extract_json(self, text: str) -> str:
        """Strip markdown code fence formatting to extract raw JSON string."""
        text = text.strip()
        match = re.search(r"```(?:json)?\s*([\s\S]*?)\s*```", text)
        if match:
            return match.group(1).strip()
        return text

    def _generate_heuristics(
        self,
        inspected: InspectedPage,
        goal: str | None,
        max_tests: int,
    ) -> TestSuite:
        """Generate high-value test cases rule-based on inspected DOM elements."""
        tests: list[TestCase] = []
        base_url = inspected.url

        # Test 1: Smoke / Page Load
        tests.append(
            TestCase(
                id="SMOKE-001",
                name=f"Verify homepage title & core elements: {inspected.title[:40]}",
                start_url=base_url,
                preconditions=[],
                goal=f"Navigate to {base_url} and verify the page loads completely.",
                actions=[
                    {
                        "action": "navigate",
                        "url": base_url,
                        "description": "Navigate to the inspected page",
                    }
                ],
                expected=[
                    Expectation(
                        type="dom",
                        description=f"Page title should contain '{inspected.title[:30]}'",
                        value=inspected.title[:30],
                    ),
                    Expectation(
                        type="url",
                        description=f"Current URL should match base URL {base_url}",
                        value=base_url,
                    ),
                ],
                tags=["smoke", "p0"],
                timeout=30,
            )
        )

        # Test 2: Search functionality (if search input exists)
        if inspected.search_inputs:
            s_input = inspected.search_inputs[0]
            sel = s_input.get("selector") or "input[type='search']"
            search_query = "chair" if "amazon" in base_url.lower() or "shop" in base_url.lower() else "test"
            tests.append(
                TestCase(
                    id="SEARCH-001",
                    name=f"Search for keyword '{search_query}'",
                    start_url=base_url,
                    preconditions=[],
                    goal=f"Locate the search input using selector '{sel}', type '{search_query}', and submit the search.",
                    actions=[
                        {
                            "action": "fill",
                            "selector": sel,
                            "value": search_query,
                            "description": "Enter the search query",
                        },
                        {
                            "action": "press",
                            "selector": sel,
                            "key": "Enter",
                            "description": "Submit the search",
                        },
                    ],
                    expected=[
                        Expectation(
                            type="dom",
                            description="Search input selector exists",
                            selector=sel,
                        ),
                        Expectation(
                            type="url",
                            description=f"URL should contain search query parameter '{search_query}'",
                            value=search_query,
                        ),
                    ],
                    tags=["search", "core"],
                    timeout=45,
                )
            )

        # Test 3: Navigation Link Check (if nav links exist)
        for idx, link in enumerate(inspected.nav_links[:2]):
            link_text = link.get("text", "").strip()
            link_href = link.get("href", "")
            if link_text and link_href and link_href != base_url:
                escaped_link_text = link_text.replace("\\", "\\\\").replace('"', '\\"')
                tests.append(
                    TestCase(
                        id=f"NAV-00{idx+1}",
                        name=f"Navigate to '{link_text}'",
                        start_url=base_url,
                        preconditions=[],
                        goal=f"Click the navigation link with text '{link_text}' and verify the destination page loads.",
                        actions=[
                            {
                                "action": "click",
                                "selector": f'a:has-text("{escaped_link_text}")',
                                "description": f"Click navigation link '{link_text}'",
                            }
                        ],
                        expected=[
                            Expectation(
                                type="url",
                                description=f"URL should navigate toward '{link_href}'",
                                value=link_href,
                            ),
                        ],
                        tags=["navigation"],
                        timeout=45,
                    )
                )

        # A button alone does not expose a post-click effect that can be verified.
        if inspected.buttons:
            logger.info(
                "Omitted generic button interaction: inspection did not expose an observable "
                "post-click effect."
            )

        # Filter to max_tests
        selected_tests = tests[:max_tests]

        suite_name = f"Auto-Generated Suite for {inspected.title or base_url}"
        if goal:
            suite_name += f" ({goal[:30]})"

        return TestSuite(
            name=suite_name,
            base_url=base_url,
            tests=selected_tests,
        )

    @staticmethod
    def _is_runnable_generated_test(test: TestCase) -> bool:
        """Return whether generated output has supported actions and deterministic evidence."""
        if not test.actions:
            return False
        meaningful = [
            expectation
            for expectation in test.expected
            if expectation.type in {"dom", "url"}
            and (expectation.selector is not None or expectation.value is not None)
        ]
        if not meaningful:
            return False
        clicked_selectors = {
            action.selector for action in test.actions if action.action == "click"
        }
        return not (
            clicked_selectors
            and all(
                expectation.type == "dom"
                and expectation.selector in clicked_selectors
                and expectation.value is None
                for expectation in meaningful
            )
        )

    def save_suite(self, suite: TestSuite, output_path: str | Path) -> Path:
        """Serialize TestSuite to a JSON file."""
        out = Path(output_path)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(suite.model_dump_json(indent=2), encoding="utf-8")
        logger.info("Saved test suite with %d tests to %s", len(suite.tests), out)
        return out
