"""LLM-powered test suite generator for autonomous web QA."""

from __future__ import annotations

import json
import logging
import os
import re
from pathlib import Path
from typing import Any
from urllib.parse import urljoin

from aiqa.models.coverage import DiscoveredFeature, FeatureRegistry
from aiqa.models.test_case import Expectation, TestCase, TestSuite
from aiqa.planner.site_inspector import InspectedPage
from aiqa.security.policy import is_origin_allowed, is_safe_url_scheme

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
  - "type": "dom", "url", "semantic", or "business_rule"
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


def decompose_goal(goal: str | None) -> dict[str, Any]:
    """Decompose a user testing goal into target business flows, role, and oracle requirements."""
    if not goal or not goal.strip():
        return {
            "raw_goal": "",
            "flows": [],
            "role": None,
            "oracle": None,
            "requires_business_oracle": False,
            "search_term": None,
            "include_negative": False,
        }

    raw = goal.strip()
    lower = raw.lower()
    flows: list[str] = []

    flow_keywords: list[tuple[str, tuple[str, ...]]] = [
        ("admin", ("admin", "rbac", "permission", "role")),
        ("auth", ("login", "sign in", "signin", "auth", "account", "logout")),
        ("search", ("search", "query", "filter", "find")),
        ("cart", ("cart", "basket", "add to cart")),
        ("checkout", ("checkout", "payment", "order", "purchase")),
        ("pricing", ("discount", "coupon", "promo", "pricing")),
        ("form", ("form", "submit", "contact", "feedback")),
        ("navigation", ("navigation", "catalog", "menu", "route")),
    ]
    for flow_name, kw_tuple in flow_keywords:
        if any(kw in lower for kw in kw_tuple):
            flows.append(flow_name)

    role: str | None = None
    if any(kw in lower for kw in ("admin", "rbac", "superuser")):
        role = "admin"
    elif any(kw in lower for kw in ("guest", "viewer", "member")):
        role = "guest" if "guest" in lower else "member"

    # Extract explicit oracle if present (e.g. "expected=90", "oracle: $80", "== 403")
    oracle: str | None = None
    oracle_match = re.search(
        r"(?:oracle|expected|equals)\s*[:=]\s*['\"]?([^'\",;]+)['\"]?",
        raw,
        flags=re.IGNORECASE,
    )
    if oracle_match:
        oracle = oracle_match.group(1).strip()

    business_rule_markers = (
        "discount",
        "coupon",
        "pricing",
        "rbac",
        "permission",
        "business rule",
    )
    requires_business_oracle = any(m in lower for m in business_rule_markers) and oracle is None

    negative_markers = (
        "negative",
        "invalid",
        "boundary",
        "edge case",
        "empty",
    )
    include_negative = any(m in lower for m in negative_markers)

    quoted = re.search(r"['\"]([^'\"]{2,30})['\"]", raw)
    search_term = quoted.group(1).strip() if quoted else None

    return {
        "raw_goal": raw,
        "flows": flows,
        "role": role,
        "oracle": oracle,
        "requires_business_oracle": requires_business_oracle,
        "search_term": search_term,
        "include_negative": include_negative,
    }


class TestPlanner:
    """Generates structured TestSuite from inspected page structure, site map, and user goals."""

    __test__ = False

    def __init__(
        self,
        api_key: str | None = None,
        base_url: str | None = None,
        model: str | None = None,
        allowed_origins: list[str] | None = None,
        allow_cross_origin: bool = False,
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

    def generate_suite(
        self,
        inspected: InspectedPage,
        goal: str | None = None,
        max_tests: int = 5,
        *,
        allowed_origins: list[str] | None = None,
        allow_cross_origin: bool | None = None,
        registry: FeatureRegistry | None = None,
        include_gap_fill: bool = True,
        role: str | None = None,
    ) -> TestSuite:
        """Generate a TestSuite using an LLM, or fallback to goal-driven heuristic generation."""
        origins = allowed_origins if allowed_origins is not None else self.allowed_origins
        cross_origin = (
            allow_cross_origin if allow_cross_origin is not None else self.allow_cross_origin
        )
        llm_error: str | None = None
        if self.api_key:
            try:
                logger.info("Generating test suite using LLM (%s)...", self.model)
                suite = self._generate_with_llm(inspected, goal, max_tests, registry=registry)
                if role:
                    for t in suite.tests:
                        if not t.role:
                            t.role = role
                return suite
            except Exception as exc:  # noqa: BLE001
                llm_error = str(exc)
                logger.warning(
                    "LLM test generation failed (%s). Falling back to heuristic generator.",
                    exc,
                )

        logger.info("Generating test suite using heuristic generator...")
        return self._generate_heuristics(
            inspected,
            goal,
            max_tests,
            allowed_origins=origins,
            allow_cross_origin=cross_origin,
            registry=registry,
            llm_fallback_reason=llm_error,
            include_gap_fill=include_gap_fill,
            role=role,
        )

    def _generate_with_llm(
        self,
        inspected: InspectedPage,
        goal: str | None,
        max_tests: int,
        *,
        registry: FeatureRegistry | None = None,
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
        if registry is not None and registry.routes:
            user_content_lines.append(f"Discovered Site Routes: {', '.join(registry.routes[:15])}")
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
        clean_json = self._extract_json(raw_output)
        data = json.loads(clean_json)

        suite = TestSuite.model_validate(data)
        invalid_ids = [
            test.id for test in suite.tests if not self._is_runnable_generated_test(test)
        ]
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
        *,
        allowed_origins: list[str] | None = None,
        allow_cross_origin: bool = False,
        registry: FeatureRegistry | None = None,
        llm_fallback_reason: str | None = None,
        include_gap_fill: bool = True,
        role: str | None = None,
    ) -> TestSuite:
        """Generate goal-driven and multi-route test cases from inspected DOM and site map."""
        tests: list[TestCase] = []
        base_url = inspected.url
        origins = [base_url, *(allowed_origins or [])]
        planning_notes: list[str] = []
        decomposed = decompose_goal(goal)
        if role and not decomposed.get("role"):
            decomposed["role"] = role
        requested_flows = decomposed["flows"]

        if goal:
            if llm_fallback_reason:
                planning_notes.append(
                    f"Capability degradation: LLM planner failed ({llm_fallback_reason}); "
                    f"fell back to heuristic goal decomposition for '{goal}'."
                )
            elif not self.api_key:
                planning_notes.append(
                    "Capability degradation: No LLM API key configured; using heuristic goal "
                    f"decomposition for '{goal}' (flows={requested_flows or ['general']})."
                )

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

        # Helper to build flow-specific tests when a goal explicitly requests specialized flows
        specific_business_flows = [
            f
            for f in requested_flows
            if f in {"admin", "auth", "cart", "checkout", "pricing", "form"}
        ]
        if specific_business_flows or decomposed.get("include_negative"):
            tests.extend(
                self._build_goal_flow_tests(
                    inspected=inspected,
                    base_url=base_url,
                    decomposed=decomposed,
                    registry=registry,
                    planning_notes=planning_notes,
                )
            )

        # Search functionality (if search input exists on start page or in registry)
        search_feat = (
            next(
                (f for f in registry.features if f.type == "search" and f.selector),
                None,
            )
            if registry is not None
            else None
        )
        if (inspected.search_inputs or search_feat is not None) and (
            not specific_business_flows or "search" in requested_flows
        ):
            if inspected.search_inputs:
                s_input = inspected.search_inputs[0]
                sel = s_input.get("selector") or "input[type='search']"
                search_start_url = base_url
            else:
                sel = str(search_feat.selector) if search_feat else "input[type='search']"
                search_start_url = (
                    search_feat.url if (search_feat and search_feat.url) else base_url
                )
            hotword_btn = next(
                (
                    b
                    for b in inspected.buttons
                    if str(b.get("testId") or "").lower().startswith("hotword-")
                    and b.get("text")
                ),
                None,
            )
            search_query = (
                decomposed.get("search_term")
                or (hotword_btn["text"] if hotword_btn else None)
                or (
                    "chair"
                    if "amazon" in base_url.lower() or "shop" in base_url.lower()
                    else "test"
                )
            )
            search_btn = next(
                (
                    b
                    for b in inspected.buttons
                    if any(
                        k in (b.get("text", "") + " " + b.get("selector", "")).lower()
                        for k in ("search", "find")
                    )
                ),
                None,
            )
            has_search_form = any(
                bool(f.get("action"))
                and f.get("method", "GET").upper() == "GET"
                and f.get("inputs")
                for f in (inspected.forms or [])
            )
            if search_btn and search_btn.get("selector") and not has_search_form:
                btn_sel = search_btn["selector"]
                search_actions = [
                    {
                        "action": "fill",
                        "selector": sel,
                        "value": search_query,
                        "description": "Enter the search query",
                    },
                    {
                        "action": "click",
                        "selector": btn_sel,
                        "description": "Click the search submit button",
                    },
                ]
                search_expected = [
                    Expectation(
                        type="dom",
                        description="Search input selector exists",
                        selector=sel,
                    ),
                    Expectation(
                        type="dom",
                        description=f"Search results update for query '{search_query}'",
                        selector="#search-results, #results, .search-results, [data-testid='product-grid'], [data-testid='product-result-count']",
                        value=search_query,
                    ),
                ]
            else:
                search_actions = [
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
                ]
                search_expected = [
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
                ]
            tests.append(
                TestCase(
                    id="SEARCH-001",
                    name=f"Search for keyword '{search_query}'",
                    start_url=search_start_url,
                    preconditions=[],
                    goal=(
                        f"Locate the search input using selector '{sel}', type '{search_query}', "
                        "and submit the search."
                    ),
                    actions=search_actions,
                    expected=search_expected,
                    tags=["search", "core"],
                    timeout=45,
                )
            )

        # Navigation Link Check (scales with max_tests when more links are present)
        nav_count = 0
        omitted_cross_origin = 0
        nav_limit = max(2, max_tests - len(tests)) if max_tests > 4 else 2
        if not specific_business_flows or "navigation" in requested_flows:
            for link in inspected.nav_links:
                if nav_count >= nav_limit:
                    break
                link_text = link.get("text", "").strip()
                link_href = link.get("href", "")
                if link_text and link_href and link_href != base_url:
                    if not is_safe_url_scheme(link_href):
                        continue
                    if not is_origin_allowed(
                        link_href, origins, allow_cross_origin=allow_cross_origin
                    ):
                        omitted_cross_origin += 1
                        continue
                    nav_count += 1
                    escaped_link_text = link_text.replace("\\", "\\\\").replace('"', '\\"')
                    tests.append(
                        TestCase(
                            id=f"NAV-{nav_count:03d}",
                            name=f"Navigate to '{link_text}'",
                            start_url=base_url,
                            preconditions=[],
                            goal=(
                                f"Click the navigation link with text '{link_text}' "
                                "and verify the destination page loads."
                            ),
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

        if omitted_cross_origin > 0:
            cross_note = (
                f"Omitted {omitted_cross_origin} cross-origin navigation link(s) outside allowed origins."
            )
            logger.info(cross_note)
            planning_notes.append(cross_note)

        # A button alone does not expose a post-click effect that can be verified.
        if inspected.buttons:
            note = (
                f"Omitted {len(inspected.buttons)} candidate button interaction(s): "
                "inspection did not expose an observable post-click effect."
            )
            logger.info(note)
            planning_notes.append(note)

        # If registry is provided and include_gap_fill is enabled, generate multi-route tests
        if include_gap_fill and registry is not None and len(tests) < max_tests:
            gap_tests = self.generate_gap_tests(
                registry=registry,
                uncovered_features=registry.features,
                existing_tests=tests,
                max_new_tests=max_tests - len(tests),
            )
            tests.extend(gap_tests)

        effective_role = decomposed.get("role")
        if effective_role:
            for t in tests:
                if not t.role:
                    t.role = effective_role

        selected_tests = tests[:max_tests]

        suite_name = f"Auto-Generated Suite for {inspected.title or base_url}"
        if goal:
            suite_name += f" ({goal[:30]})"

        return TestSuite(
            name=suite_name,
            base_url=base_url,
            tests=selected_tests,
            planning_notes=planning_notes,
            allowed_origins=list(allowed_origins or []),
            allow_cross_origin=allow_cross_origin,
        )

    def _build_goal_flow_tests(
        self,
        inspected: InspectedPage,
        base_url: str,
        decomposed: dict[str, Any],
        registry: FeatureRegistry | None,
        planning_notes: list[str],
    ) -> list[TestCase]:
        """Construct targeted TestCases corresponding to decomposed user goal flows."""
        flow_tests: list[TestCase] = []
        flows: list[str] = decomposed["flows"]
        oracle: str | None = decomposed.get("oracle")
        role: str | None = decomposed.get("role")
        features = list(registry.features) if registry is not None else []
        pages = list(registry.pages) if registry is not None else []
        routes = list(registry.routes) if registry is not None else []

        if "auth" in flows:
            auth_feat = next(
                (
                    f
                    for f in features
                    if f.type == "auth"
                    and any(k in f.url.lower() for k in ("/login", "/signin", "/auth"))
                ),
                None,
            )
            auth_route = next(
                (r for r in routes if any(k in r.lower() for k in ("/login", "/signin", "/auth"))),
                None,
            )
            auth_link = next(
                (
                    l
                    for l in inspected.nav_links
                    if any(
                        k in (l.get("text", "") + " " + l.get("href", "")).lower()
                        for k in ("login", "signin", "sign in", "account", "auth")
                    )
                ),
                None,
            )
            target_url = (
                auth_feat.url
                if auth_feat
                else (
                    auth_route
                    or (auth_link.get("href") if auth_link else None)
                    or urljoin(base_url.rstrip("/") + "/", "login")
                )
            )
            flow_tests.append(
                TestCase(
                    id="AUTH-001",
                    name="Verify authentication portal & sign-in flow",
                    start_url=base_url,
                    preconditions=[],
                    goal=f"Navigate to authentication portal ({target_url}) and verify login controls.",
                    actions=[
                        {
                            "action": "navigate",
                            "url": target_url,
                            "description": "Navigate to login/authentication route",
                        }
                    ],
                    expected=[
                        Expectation(
                            type="url",
                            description=f"URL should resolve to authentication route '{target_url}'",
                            value=target_url,
                        ),
                    ],
                    tags=["auth", "p0"],
                    timeout=45,
                )
            )

        if "admin" in flows:
            admin_feat = next(
                (f for f in features if f.type == "admin" and "/admin" in f.url.lower()),
                None,
            )
            admin_route = next((r for r in routes if "/admin" in r.lower()), None)
            admin_link = next(
                (
                    l
                    for l in inspected.nav_links
                    if any(
                        k in (l.get("text", "") + " " + l.get("href", "")).lower()
                        for k in ("admin",)
                    )
                ),
                None,
            )
            admin_url = (
                admin_feat.url
                if admin_feat
                else (
                    admin_route
                    or (admin_link.get("href") if admin_link else None)
                    or urljoin(base_url.rstrip("/") + "/", "admin")
                )
            )
            if oracle is None:
                planning_notes.append(
                    "Business rule oracle missing for admin RBAC policy; marking permission assertion "
                    "with inconclusive_if_missing_oracle=True."
                )
            flow_tests.append(
                TestCase(
                    id="ADMIN-RBAC-001",
                    name="Verify administrator RBAC permission boundary",
                    start_url=base_url,
                    role=role or "admin",
                    preconditions=[],
                    goal=f"Access administrative route {admin_url} and verify RBAC permission policy.",
                    actions=[
                        {
                            "action": "navigate",
                            "url": admin_url,
                            "description": "Navigate to administrative route",
                        }
                    ],
                    expected=[
                        Expectation(
                            type="url",
                            description=f"Attempt navigation to admin route '{admin_url}'",
                            value=admin_url,
                        ),
                        Expectation(
                            type="business_rule",
                            description="Verify role-based access control policy for administrator route",
                            oracle=oracle,
                            value=oracle,
                            inconclusive_if_missing_oracle=True,
                        ),
                    ],
                    tags=["admin", "rbac", "security"],
                    timeout=45,
                )
            )

        cart_test_id: str | None = None
        if "cart" in flows:
            cart_action_feat = next(
                (
                    f
                    for f in features
                    if f.type == "cart"
                    and f.selector
                    and (
                        f.id.startswith("feat_cart_action")
                        or (
                            not f.selector.startswith("a")
                            and "nav" not in f.selector.lower()
                            and "link" not in f.selector.lower()
                            and "header-cart" not in f.selector.lower()
                        )
                    )
                ),
                None,
            )
            cart_feat = next(
                (f for f in features if f.type == "cart" and "/cart" in f.url.lower()),
                None,
            )
            cart_route = next((r for r in routes if "/cart" in r.lower()), None)
            cart_btn = next(
                (
                    b
                    for b in inspected.buttons
                    if any(
                        k in (b.get("text", "") + " " + b.get("selector", "")).lower()
                        for k in ("cart", "add-to-cart", "add-cart")
                    )
                ),
                None,
            )
            cart_link = next(
                (
                    l
                    for l in inspected.nav_links
                    if any(
                        k in (l.get("text", "") + " " + l.get("href", "")).lower()
                        for k in ("cart", "basket")
                    )
                ),
                None,
            )
            cart_test_id = "CART-001"
            if (cart_btn and cart_btn.get("selector")) or cart_action_feat is not None:
                if cart_btn and cart_btn.get("selector"):
                    sel = cart_btn["selector"]
                    target_start = base_url
                    btn_label = cart_btn.get("text", "Add to Cart")
                else:
                    sel = str(cart_action_feat.selector)  # type: ignore[union-attr]
                    target_start = (
                        cart_action_feat.url  # type: ignore[union-attr]
                        if (cart_action_feat and cart_action_feat.url)
                        else base_url
                    )
                    btn_label = (
                        cart_action_feat.name if cart_action_feat else "Add to Cart"
                    )
                flow_tests.append(
                    TestCase(
                        id=cart_test_id,
                        name="Add item to shopping cart",
                        start_url=target_start,
                        preconditions=[],
                        goal=f"Click '{btn_label}' ({sel}) and verify cart state updates.",
                        actions=[
                            {
                                "action": "click",
                                "selector": sel,
                                "description": "Click Add to Cart button",
                            }
                        ],
                        expected=[
                            Expectation(
                                type="dom",
                                description="Cart count updates to 1 in DOM",
                                selector="#cart-count, .cart-count, #cart-status, [data-testid='nav-cart-count'], [data-testid='cart-count']",
                                value="1",
                            )
                        ],
                        tags=["cart", "core"],
                        timeout=45,
                    )
                )
            else:
                target_cart_url = (
                    cart_feat.url
                    if cart_feat
                    else (
                        cart_route
                        or (cart_link.get("href") if cart_link else None)
                        or urljoin(base_url.rstrip("/") + "/", "cart")
                    )
                )
                flow_tests.append(
                    TestCase(
                        id=cart_test_id,
                        name="Open shopping cart view",
                        start_url=base_url,
                        preconditions=[],
                        goal=f"Navigate to shopping cart ({target_cart_url}) and verify cart contents.",
                        actions=[
                            {
                                "action": "navigate",
                                "url": target_cart_url,
                                "description": "Open shopping cart route",
                            }
                        ],
                        expected=[
                            Expectation(
                                type="url",
                                description=f"URL should point to shopping cart '{target_cart_url}'",
                                value=target_cart_url,
                            )
                        ],
                        tags=["cart", "core"],
                        timeout=45,
                    )
                )

        if "checkout" in flows:
            checkout_feat = next(
                (f for f in features if f.type == "checkout" and "checkout" in f.url.lower()),
                None,
            )
            checkout_link = next(
                (
                    l
                    for l in inspected.nav_links
                    if any(
                        k in (l.get("text", "") + " " + l.get("href", "")).lower()
                        for k in ("checkout",)
                    )
                ),
                None,
            )
            checkout_url = (
                checkout_feat.url
                if checkout_feat
                else (
                    checkout_link.get("href")
                    if checkout_link
                    else urljoin(base_url.rstrip("/") + "/", "checkout")
                )
            )
            if checkout_url.rstrip("/") == base_url.rstrip("/"):
                checkout_route_candidate = next(
                    (r for r in routes if "checkout" in r.lower()),
                    None,
                )
                if checkout_route_candidate:
                    checkout_url = checkout_route_candidate
                else:
                    checkout_url = urljoin(base_url.rstrip("/") + "/", "checkout")

            checkout_page = next(
                (
                    p
                    for p in pages
                    if str(p.get("url", "")).rstrip("/") == checkout_url.rstrip("/")
                ),
                None,
            )
            checkout_inputs: list[dict[str, Any]] = []
            checkout_btn: dict[str, Any] | None = None
            if checkout_page is not None:
                for form in checkout_page.get("forms") or []:
                    for inp in form.get("inputs") or []:
                        sel = inp.get("selector") or inp.get("id")
                        if sel:
                            checkout_inputs.append({**inp, "selector": sel})
                for inp in checkout_page.get("inputs") or []:
                    sel = inp.get("selector") or inp.get("id")
                    if sel and not any(x.get("selector") == sel for x in checkout_inputs):
                        checkout_inputs.append({**inp, "selector": sel})
                for btn in checkout_page.get("buttons") or []:
                    btn_sel = btn.get("selector") or btn.get("id")
                    btn_label = (btn.get("text", "") + " " + str(btn_sel or "")).lower()
                    if btn_sel and any(
                        k in btn_label for k in ("order", "pay", "checkout", "submit", "place")
                    ):
                        checkout_btn = {**btn, "selector": btn_sel}
                        break

            deps = [cart_test_id] if cart_test_id else []
            if checkout_btn and checkout_btn.get("selector"):
                checkout_actions: list[dict[str, Any]] = []
                if checkout_inputs:
                    first_inp = checkout_inputs[0]
                    checkout_actions.append(
                        {
                            "action": "fill",
                            "selector": first_inp["selector"],
                            "value": "Alice Tester",
                            "description": "Fill checkout customer details",
                        }
                    )
                checkout_actions.append(
                    {
                        "action": "click",
                        "selector": checkout_btn["selector"],
                        "description": f"Submit checkout order via {checkout_btn['selector']}",
                    }
                )
                flow_tests.append(
                    TestCase(
                        id="CHECKOUT-001",
                        name="Proceed through checkout settlement flow",
                        start_url=checkout_url,
                        depends_on=[],
                        preconditions=[],
                        goal=(
                            f"Complete checkout on {checkout_url}, click '{checkout_btn.get('text', 'Place Order')}', "
                            "and verify order confirmation."
                        ),
                        actions=checkout_actions,
                        expected=[
                            Expectation(
                                type="url",
                                description=f"URL should resolve to checkout route '{checkout_url}'",
                                value=checkout_url,
                            ),
                            Expectation(
                                type="dom",
                                description="Order confirmation should reflect completed order",
                                selector="#order-confirmation, .order-confirmation, #order-status",
                                value="Order confirmed",
                            ),
                        ],
                        tags=["checkout", "p0"],
                        timeout=45,
                    )
                )
            else:
                flow_tests.append(
                    TestCase(
                        id="CHECKOUT-001",
                        name="Proceed through checkout settlement flow",
                        start_url=base_url,
                        depends_on=deps,
                        preconditions=[f"Requires {cart_test_id}" for _ in deps],
                        goal=f"Navigate to checkout ({checkout_url}) and verify order settlement summary.",
                        actions=[
                            {
                                "action": "navigate",
                                "url": checkout_url,
                                "description": "Navigate to checkout route",
                            }
                        ],
                        expected=[
                            Expectation(
                                type="url",
                                description=f"URL should resolve to checkout route '{checkout_url}'",
                                value="checkout" if checkout_page is None else checkout_url,
                            )
                        ],
                        tags=["checkout", "p0"],
                        timeout=45,
                    )
                )

        if "pricing" in flows:
            pricing_feat = next((f for f in features if f.type == "pricing"), None)
            start = pricing_feat.url if pricing_feat else base_url
            if oracle is None:
                planning_notes.append(
                    "Business rule oracle missing for pricing/discount calculation; marking "
                    "expectation with inconclusive_if_missing_oracle=True."
                )
            flow_tests.append(
                TestCase(
                    id="RULE-DISCOUNT-001",
                    name="Verify promotional discount & pricing calculation rule",
                    start_url=start,
                    preconditions=[],
                    goal="Apply promotional discount / coupon and verify the expected final price calculation.",
                    actions=[
                        {
                            "action": "navigate",
                            "url": start,
                            "description": "Open pricing/checkout page for discount verification",
                        }
                    ],
                    expected=[
                        Expectation(
                            type="business_rule",
                            description="Discounted total matches business pricing formula",
                            oracle=oracle,
                            value=oracle,
                            inconclusive_if_missing_oracle=True,
                        )
                    ],
                    tags=["pricing", "business_rule"],
                    timeout=45,
                )
            )

        if "form" in flows or decomposed.get("include_negative"):
            form_feat = next((f for f in features if f.type == "form"), None)
            form_url = form_feat.url if form_feat else base_url
            target_forms = list(inspected.forms or [])
            if not target_forms and pages:
                for p in pages:
                    if p.get("forms") or p.get("inputs"):
                        target_forms = list(p.get("forms") or [])
                        form_url = str(p.get("url") or form_url)
                        break
            first_input_sel: str | None = None
            if target_forms and target_forms[0].get("inputs"):
                first_input_sel = (
                    target_forms[0]["inputs"][0].get("selector")
                    or target_forms[0]["inputs"][0].get("id")
                )
            elif pages:
                for p in pages:
                    if str(p.get("url", "")).rstrip("/") == form_url.rstrip("/"):
                        for inp in p.get("inputs") or []:
                            first_input_sel = inp.get("selector") or inp.get("id")
                            if first_input_sel:
                                break
            if not first_input_sel and inspected.search_inputs:
                first_input_sel = inspected.search_inputs[0].get("selector")

            if "form" in flows and first_input_sel:
                flow_tests.append(
                    TestCase(
                        id="FORM-001",
                        name="Verify form input and submission",
                        start_url=form_url,
                        preconditions=[],
                        goal=f"Fill form input '{first_input_sel}' on {form_url} and submit.",
                        actions=[
                            {
                                "action": "fill",
                                "selector": first_input_sel,
                                "value": "QA Verification Input",
                                "description": f"Fill form field {first_input_sel}",
                            },
                            {
                                "action": "press",
                                "selector": first_input_sel,
                                "key": "Enter",
                                "description": "Submit form via Enter key",
                            },
                        ],
                        expected=[
                            Expectation(
                                type="dom",
                                description=f"Form field {first_input_sel} remains accessible after input",
                                selector=first_input_sel,
                            )
                        ],
                        tags=["form", "core"],
                        timeout=45,
                    )
                )

            if decomposed.get("include_negative") and first_input_sel:
                flow_tests.append(
                    TestCase(
                        id="FORM-NEG-001",
                        name="Verify boundary / abnormal input handling",
                        start_url=form_url,
                        preconditions=[],
                        goal=(
                            f"Submit boundary/special-character input in '{first_input_sel}' on {form_url} "
                            "and verify the page does not crash."
                        ),
                        actions=[
                            {
                                "action": "fill",
                                "selector": first_input_sel,
                                "value": "   ",
                                "description": "Fill whitespace-only boundary input",
                            },
                            {
                                "action": "press",
                                "selector": first_input_sel,
                                "key": "Enter",
                                "description": "Attempt submission with boundary value",
                            },
                        ],
                        expected=[
                            Expectation(
                                type="dom",
                                description="Page remains stable with input control rendered",
                                selector=first_input_sel,
                            )
                        ],
                        tags=["form", "negative", "boundary"],
                        timeout=45,
                    )
                )

        return flow_tests

    def generate_gap_tests(
        self,
        registry: FeatureRegistry,
        uncovered_features: list[DiscoveredFeature],
        existing_tests: list[TestCase],
        max_new_tests: int = 5,
    ) -> list[TestCase]:
        """Generate targeted follow-up tests for uncovered features discovered across routes."""
        if max_new_tests <= 0:
            return []

        existing_ids = {t.id for t in existing_tests}
        raw_blocked = registry.blocked_routes or []
        if isinstance(raw_blocked, dict):
            blocked = set(raw_blocked.keys())
        else:
            blocked = {
                str(item.get("url") or item.get("route") or "")
                for item in raw_blocked
                if isinstance(item, dict)
            }
        new_tests: list[TestCase] = []
        counter = 1

        for feat in uncovered_features:
            if len(new_tests) >= max_new_tests:
                break
            if feat.url in blocked:
                continue

            ftype = feat.type.lower()
            route_url = feat.url or registry.base_url

            is_cart_action = bool(
                feat.id.startswith("feat_cart_action")
                or "add to cart" in feat.name.lower()
                or (
                    feat.selector
                    and (
                        "add-cart" in feat.selector.lower()
                        or "add-to-cart" in feat.selector.lower()
                    )
                )
            )
            is_link_selector = bool(
                feat.selector
                and not is_cart_action
                and (
                    feat.id.startswith(("feat_cart", "feat_checkout", "feat_auth", "feat_admin"))
                    or feat.selector.startswith("a")
                    or "nav" in feat.selector.lower()
                    or "link" in feat.selector.lower()
                    or ftype in ("admin", "auth")
                )
            )

            # Avoid duplicate coverage if an existing test already targets this exact selector on this route,
            # or if this is a shared navigation link already exercised in the suite
            norm_feat_sel = (feat.selector or "").strip().replace('"', "'")
            if norm_feat_sel and any(
                (is_link_selector or t.start_url.rstrip("/") == route_url.rstrip("/"))
                and any(
                    str(getattr(a, "selector", "") or "").strip().replace('"', "'")
                    == norm_feat_sel
                    for a in (t.actions or [])
                )
                for t in [*existing_tests, *new_tests]
            ):
                continue

            if ftype in ("cart", "checkout", "auth", "admin") and is_link_selector:
                target_token = ftype if ftype != "auth" else "login"
                if f"/{target_token}" in route_url.lower():
                    continue

            while f"GAP-{counter:03d}" in existing_ids:
                counter += 1
            tid = f"GAP-{counter:03d}"
            existing_ids.add(tid)

            if ftype == "search" and feat.selector:
                new_tests.append(
                    TestCase(
                        id=tid,
                        name=f"Gap coverage: {feat.name}",
                        start_url=route_url,
                        preconditions=[],
                        goal=f"Search using '{feat.selector}' on {route_url} and verify results.",
                        actions=[
                            {
                                "action": "fill",
                                "selector": feat.selector,
                                "value": "test",
                                "description": f"Fill search input {feat.selector}",
                            },
                            {
                                "action": "press",
                                "selector": feat.selector,
                                "key": "Enter",
                                "description": "Submit search query",
                            },
                        ],
                        expected=[
                            Expectation(
                                type="dom",
                                description=f"Search input {feat.selector} present",
                                selector=feat.selector,
                            ),
                            Expectation(
                                type="url",
                                description="URL reflects search submission",
                                value="test",
                            ),
                        ],
                        tags=["search", "gap"],
                        timeout=45,
                    )
                )
            elif ftype in ("cart", "checkout", "auth", "admin"):
                if feat.selector and is_link_selector:
                    expected_list = [
                        Expectation(
                            type="url",
                            description=f"URL updates for {ftype} flow",
                            value=ftype if ftype != "auth" else "login",
                        )
                    ]
                    if ftype == "admin":
                        expected_list.append(
                            Expectation(
                                type="dom",
                                description="Admin destination renders main content without server error",
                                selector="main, #admin-portal, #admin-audit-table, [data-testid='admin-dashboard-page']",
                            )
                        )
                    new_tests.append(
                        TestCase(
                            id=tid,
                            name=f"Gap coverage: {feat.name}",
                            start_url=route_url,
                            preconditions=[],
                            goal=f"Click '{feat.name}' ({feat.selector}) on {route_url}.",
                            actions=[
                                {
                                    "action": "click",
                                    "selector": feat.selector,
                                    "description": f"Click {feat.name}",
                                }
                            ],
                            expected=expected_list,
                            tags=[ftype, "gap"],
                            timeout=45,
                        )
                    )
                elif feat.selector:
                    new_tests.append(
                        TestCase(
                            id=tid,
                            name=f"Gap coverage: {feat.name}",
                            start_url=route_url,
                            preconditions=[],
                            goal=f"Interact with '{feat.name}' ({feat.selector}) on {route_url}.",
                            actions=[
                                {
                                    "action": "click",
                                    "selector": feat.selector,
                                    "description": f"Click {feat.name}",
                                }
                            ],
                            expected=[
                                Expectation(
                                    type="dom",
                                    description=f"Verify observable state after clicking {feat.name}",
                                    selector="#cart-count, .cart-count, #cart-status, [data-testid='nav-cart-count'], [data-testid='cart-count']"
                                    if ftype == "cart"
                                    else "body",
                                    value="1"
                                    if ftype == "cart"
                                    else (feat.name.split(":")[-1].strip(" '")[:15] or "Cart"),
                                )
                            ],
                            tags=[ftype, "gap"],
                            timeout=45,
                        )
                    )
                else:
                    new_tests.append(
                        TestCase(
                            id=tid,
                            name=f"Gap coverage: {feat.name}",
                            start_url=route_url,
                            preconditions=[],
                            goal=f"Navigate to {route_url} to verify {feat.name}.",
                            actions=[
                                {
                                    "action": "navigate",
                                    "url": route_url,
                                    "description": f"Navigate to {route_url}",
                                }
                            ],
                            expected=[
                                Expectation(
                                    type="url",
                                    description=f"URL matches {route_url}",
                                    value=route_url,
                                )
                            ],
                            tags=[ftype, "gap"],
                            timeout=45,
                        )
                    )
            elif ftype == "pricing" and feat.selector:
                new_tests.append(
                    TestCase(
                        id=tid,
                        name=f"Gap coverage: {feat.name}",
                        start_url=route_url,
                        preconditions=[],
                        goal=f"Enter promo code in {feat.selector} on {route_url}.",
                        actions=[
                            {
                                "action": "fill",
                                "selector": feat.selector,
                                "value": "SAVE10",
                                "description": "Fill promo/discount input",
                            }
                        ],
                        expected=[
                            Expectation(
                                type="business_rule",
                                description=f"Verify discount calculation for {feat.name}",
                                inconclusive_if_missing_oracle=True,
                            )
                        ],
                        tags=["pricing", "gap"],
                        timeout=45,
                    )
                )
            elif ftype == "form":
                page_entry = next(
                    (
                        p
                        for p in (registry.pages or [])
                        if str(p.get("url", "")).rstrip("/") == route_url.rstrip("/")
                    ),
                    None,
                )
                input_sel: str | None = None
                if page_entry:
                    for form in page_entry.get("forms") or []:
                        for inp in form.get("inputs") or []:
                            if inp.get("selector") or inp.get("id"):
                                input_sel = inp.get("selector") or inp.get("id")
                                break
                        if input_sel:
                            break
                    if not input_sel:
                        for inp in page_entry.get("inputs") or []:
                            if inp.get("selector") or inp.get("id"):
                                input_sel = inp.get("selector") or inp.get("id")
                                break
                if input_sel:
                    new_tests.append(
                        TestCase(
                            id=tid,
                            name=f"Gap coverage: {feat.name}",
                            start_url=route_url,
                            preconditions=[],
                            goal=f"Fill form input '{input_sel}' on {route_url} and verify form state.",
                            actions=[
                                {
                                    "action": "fill",
                                    "selector": input_sel,
                                    "value": "test-input",
                                    "description": f"Fill form input {input_sel}",
                                },
                                {
                                    "action": "press",
                                    "selector": input_sel,
                                    "key": "Enter",
                                    "description": "Submit form input",
                                },
                            ],
                            expected=[
                                Expectation(
                                    type="dom",
                                    description=f"Form input {input_sel} remains rendered",
                                    selector=input_sel,
                                )
                            ],
                            tags=["form", "gap"],
                            timeout=45,
                        )
                    )
            elif ftype in ("navigation", "interaction") and feat.selector:
                new_tests.append(
                    TestCase(
                        id=tid,
                        name=f"Gap coverage: {feat.name}",
                        start_url=route_url,
                        preconditions=[],
                        goal=f"Click '{feat.name}' ({feat.selector}) on {route_url} and verify page state.",
                        actions=[
                            {
                                "action": "click",
                                "selector": feat.selector,
                                "description": f"Click {feat.name}",
                            }
                        ],
                        expected=[
                            Expectation(
                                type="url",
                                description=f"Page remains on valid route after clicking {feat.name}",
                                value=registry.base_url,
                            )
                        ],
                        tags=[ftype, "gap"],
                        timeout=45,
                    )
                )

        return new_tests

    @staticmethod
    def _is_runnable_generated_test(test: TestCase) -> bool:
        """Return whether generated output has supported actions and deterministic evidence."""
        if not test.actions:
            return False
        meaningful = [
            expectation
            for expectation in test.expected
            if expectation.type in {"dom", "url", "business_rule"}
            and (
                expectation.selector is not None
                or expectation.value is not None
                or expectation.oracle is not None
                or expectation.inconclusive_if_missing_oracle
            )
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
