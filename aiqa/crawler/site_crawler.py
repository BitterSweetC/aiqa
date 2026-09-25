"""Multi-route crawler for discovering site architecture and building a FeatureRegistry."""

from __future__ import annotations

import logging
from collections import deque
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from aiqa.executor.browser_session import BrowserSession
from aiqa.models.coverage import DiscoveredFeature, FeatureRegistry

logger = logging.getLogger(__name__)


class SiteCrawler:
    """Explores web application routes and maps out testable features, forms, and blocked routes."""

    def __init__(
        self,
        headless: bool = True,
        cdp_url: str | None = None,
        max_pages: int = 5,
        max_depth: int = 2,
        timeout: int = 20000,
        storage_state: Path | str | dict[str, Any] | None = None,
    ) -> None:
        self.headless = headless
        self.cdp_url = cdp_url
        self.max_pages = max_pages
        self.max_depth = max_depth
        self.timeout = timeout
        self.storage_state = storage_state

    async def crawl(self, start_url: str) -> FeatureRegistry:
        """Crawl site starting from start_url and construct FeatureRegistry."""
        logger.info("Starting multi-route crawl from %s (max_pages=%d)", start_url, self.max_pages)
        parsed_start = urlparse(start_url)
        base_domain = parsed_start.netloc

        visited: set[str] = set()
        queue: deque[tuple[str, int]] = deque([(start_url, 0)])

        features: list[DiscoveredFeature] = []
        routes: list[str] = []
        pages: list[dict[str, Any]] = []
        blocked_routes: dict[str, str] = {}
        feature_id_counts: dict[str, int] = {}

        def _get_unique_id(prefix: str) -> str:
            count = feature_id_counts.get(prefix, 0) + 1
            feature_id_counts[prefix] = count
            return f"{prefix}_{count:03d}"

        async with BrowserSession(
            headless=self.headless,
            cdp_url=self.cdp_url,
            timeout=self.timeout,
            storage_state=self.storage_state,
        ) as session:
            while queue and len(visited) < self.max_pages:
                current_url, depth = queue.popleft()
                clean_url = current_url.split("#")[0].rstrip("/")
                if clean_url in visited:
                    continue

                visited.add(clean_url)

                try:
                    logger.debug("Crawling route: %s (depth=%d)", clean_url, depth)
                    nav_response = None
                    if getattr(session, "page", None) is not None and hasattr(session.page, "goto"):
                        nav_response = await session.page.goto(
                            clean_url,
                            wait_until="domcontentloaded",
                            timeout=self.timeout,
                        )
                        try:
                            has_ext_scripts = await session.page.evaluate(
                                "() => document.querySelectorAll('script[src]').length > 0"
                            )
                            if has_ext_scripts and hasattr(session.page, "wait_for_timeout"):
                                await session.page.wait_for_timeout(250)
                        except Exception as exc:  # noqa: BLE001
                            logger.debug("Hydration wait skipped: %s", exc)
                    else:
                        await session.goto(clean_url)

                    status_code = getattr(nav_response, "status", None) if nav_response else None
                    if isinstance(status_code, int) and status_code >= 400:
                        blocked_routes[clean_url] = f"HTTP {status_code}"
                        continue

                    actual_url = (
                        getattr(session.page, "url", clean_url)
                        if getattr(session, "page", None)
                        else clean_url
                    )
                    clean_actual = str(actual_url).split("#")[0].rstrip("/")
                    if (
                        clean_actual != clean_url
                        and any(tok in clean_actual.lower() for tok in ("/login", "/signin", "/auth"))
                        and not any(tok in clean_url.lower() for tok in ("/login", "/signin", "/auth"))
                    ):
                        blocked_routes[clean_url] = f"Redirected to login wall ({clean_actual})"
                        continue

                    routes.append(clean_url)

                    # Extract interactive features on this page
                    page_data = await session.evaluate_js("""
                        () => {
                            const internalLinks = [];
                            const forms = [];
                            const buttons = [];
                            const inputs = [];
                            const title = document.title || '';
                            const bodyText = (document.body ? document.body.innerText : '').trim().substring(0, 400);

                            // Extract internal links
                            const anchors = document.querySelectorAll('a[href]');
                            for (const a of Array.from(anchors).slice(0, 50)) {
                                const text = (a.innerText || a.getAttribute('aria-label') || '').trim();
                                const href = a.getAttribute('href') || '';
                                const id = a.id ? '#' + a.id : '';
                                const testId = a.getAttribute('data-testid') || '';
                                if (href && !href.startsWith('#') && !href.startsWith('javascript:')) {
                                    internalLinks.push({ text: text.substring(0, 50), href: a.href, id, testId });
                                }
                            }

                            // Extract forms
                            const formElements = document.querySelectorAll('form');
                            for (const f of Array.from(formElements).slice(0, 15)) {
                                const id = f.id ? '#' + f.id : '';
                                const action = f.getAttribute('action') || '';
                                const method = (f.getAttribute('method') || 'get').toLowerCase();
                                forms.push({ id, action, method });
                            }

                            // Extract inputs
                            const inpElements = document.querySelectorAll('input, select, textarea');
                            for (const inp of Array.from(inpElements).slice(0, 25)) {
                                const type = (inp.type || 'text').toLowerCase();
                                const name = inp.name || '';
                                const placeholder = inp.placeholder || '';
                                const id = inp.id ? '#' + inp.id : '';
                                const testId = inp.getAttribute('data-testid') || '';
                                const selector = id || (testId ? `[data-testid='${testId}']` : (name ? `input[name='${name}']` : ''));
                                inputs.push({ type, name, placeholder, id, testId, selector });
                            }

                            // Extract buttons
                            const btnElements = document.querySelectorAll('button, input[type="submit"], [role="button"]');
                            for (const btn of Array.from(btnElements).slice(0, 30)) {
                                const text = (btn.innerText || btn.value || btn.getAttribute('aria-label') || '').trim();
                                if (text) {
                                    const id = btn.id ? '#' + btn.id : '';
                                    const testId = btn.getAttribute('data-testid') || '';
                                    const selector = id || (testId ? `[data-testid='${testId}']` : '');
                                    const onclick = btn.getAttribute('onclick') || '';
                                    const type = (btn.getAttribute('type') || '').toLowerCase();
                                    const controls = btn.getAttribute('aria-controls') || btn.getAttribute('data-target') || '';
                                    const hasEffect = Boolean(onclick || controls || testId || type === 'submit' || btn.closest('form'));
                                    buttons.push({ text: text.substring(0, 50), id, testId, selector, has_observable_effect: hasEffect });
                                }
                            }

                            return { title, bodyText, internalLinks, forms, inputs, buttons };
                        }
                    """)

                    body_lower = (page_data.get("bodyText") or "").lower()
                    if (
                        any(
                            marker in body_lower
                            for marker in (
                                "403 forbidden",
                                "access denied",
                                "unauthorized",
                                "permission denied",
                            )
                        )
                        and clean_url != start_url.split("#")[0].rstrip("/")
                    ):
                        blocked_routes[clean_url] = "Access denied / insufficient role permissions"

                    pages.append(
                        {
                            "url": clean_url,
                            "title": page_data.get("title", ""),
                            "text_snippet": page_data.get("bodyText", ""),
                            "forms": page_data.get("forms", []),
                            "inputs": page_data.get("inputs", []),
                            "buttons": page_data.get("buttons", []),
                            "links": page_data.get("internalLinks", []),
                        }
                    )

                    # 1. Classify Search & Form Input Features
                    for inp in page_data.get("inputs", []):
                        name_lower = (inp.get("name") or "").lower()
                        type_lower = (inp.get("type") or "").lower()
                        place_raw = inp.get("placeholder") or ""
                        place_lower = place_raw.lower()
                        test_id_lower = (inp.get("testId") or "").lower()
                        sel = (
                            inp.get("selector")
                            or inp.get("id")
                            or (
                                f"input[name='{inp.get('name')}']"
                                if inp.get("name")
                                else f"input[type='{type_lower or 'text'}']"
                            )
                        )
                        if (
                            "search" in name_lower
                            or "search" in place_lower
                            or "search" in test_id_lower
                            or type_lower == "search"
                            or "query" in name_lower
                            or name_lower in ("q", "s", "kw")
                        ):
                            features.append(
                                DiscoveredFeature(
                                    id=_get_unique_id("feat_search"),
                                    name=f"Search Bar: {inp.get('placeholder') or inp.get('name') or 'Search'}",
                                    type="search",
                                    url=clean_url,
                                    selector=sel,
                                    description="Product/content keyword search input",
                                )
                            )
                        elif (
                            "coupon" in name_lower
                            or "discount" in name_lower
                            or "promo" in name_lower
                            or "coupon" in place_lower
                            or "coupon" in test_id_lower
                        ):
                            features.append(
                                DiscoveredFeature(
                                    id=_get_unique_id("feat_discount"),
                                    name=f"Discount / Coupon Input: {inp.get('placeholder') or inp.get('name') or 'Coupon'}",
                                    type="pricing",
                                    url=clean_url,
                                    selector=sel,
                                    description="Promotional coupon or discount calculation input",
                                )
                            )

                    # 2. Classify Cart, Checkout, Auth & Admin Links
                    for link in page_data.get("internalLinks", []):
                        l_text_raw = link.get("text", "")
                        l_text = l_text_raw.lower()
                        l_href = link.get("href", "").lower()
                        l_testid = link.get("testId") or ""
                        l_sel = (
                            link.get("id")
                            or (f"[data-testid='{l_testid}']" if l_testid else None)
                            or (
                                f"a[href*='{l_href.split('/')[-1]}']"
                                if l_href.split("/")[-1]
                                else None
                            )
                        )
                        if (
                            "cart" in l_text
                            or "cart" in l_href
                            or "basket" in l_text
                        ):
                            features.append(
                                DiscoveredFeature(
                                    id=_get_unique_id("feat_cart"),
                                    name=f"Shopping Cart: '{l_text_raw or 'Cart'}'",
                                    type="cart",
                                    url=clean_url,
                                    selector=l_sel,
                                    description="View items currently in shopping cart",
                                )
                            )
                        elif (
                            "checkout" in l_text
                            or "checkout" in l_href
                        ):
                            features.append(
                                DiscoveredFeature(
                                    id=_get_unique_id("feat_checkout"),
                                    name=f"Checkout Flow: '{l_text_raw or 'Checkout'}'",
                                    type="checkout",
                                    url=clean_url,
                                    selector=l_sel,
                                    description="Order checkout and payment gateway flow",
                                )
                            )
                        elif (
                            "login" in l_text
                            or "sign in" in l_text
                            or "account" in l_text
                        ):
                            features.append(
                                DiscoveredFeature(
                                    id=_get_unique_id("feat_auth"),
                                    name=f"Authentication: '{l_text_raw or 'Login'}'",
                                    type="auth",
                                    url=clean_url,
                                    selector=l_sel,
                                    description="User authentication, sign-in or account portal",
                                )
                            )
                        elif (
                            "admin" in l_text
                            or "/admin" in l_href
                        ):
                            features.append(
                                DiscoveredFeature(
                                    id=_get_unique_id("feat_admin"),
                                    name=f"Admin Portal: '{l_text_raw or 'Admin'}'",
                                    type="admin",
                                    url=clean_url,
                                    selector=l_sel,
                                    description="Administrative portal or RBAC restricted route",
                                )
                            )

                    # 3. Classify Buttons
                    for btn in page_data.get("buttons", []):
                        b_text = btn.get("text", "")
                        b_text_lower = b_text.lower()
                        b_testid = (btn.get("testId") or "").lower()
                        b_sel = btn.get("selector") or btn.get("id") or None
                        if (
                            "cart" in b_text_lower
                            or "add-cart" in b_testid
                            or "add-to-cart" in b_testid
                        ):
                            features.append(
                                DiscoveredFeature(
                                    id=_get_unique_id("feat_cart_action"),
                                    name=f"Add to Cart Action: '{b_text}'",
                                    type="cart",
                                    url=clean_url,
                                    selector=b_sel,
                                    description="Add selected product to shopping cart",
                                )
                            )
                        else:
                            features.append(
                                DiscoveredFeature(
                                    id=_get_unique_id("feat_btn"),
                                    name=f"Button Action: '{b_text}'",
                                    type="interaction",
                                    url=clean_url,
                                    selector=b_sel,
                                    description=f"Clickable action button '{b_text}'",
                                )
                            )

                    # 4. Enqueue internal links for subsequent depth crawling
                    if depth < self.max_depth:
                        for link in page_data.get("internalLinks", []):
                            next_href = link.get("href")
                            if next_href:
                                parsed_next = urlparse(next_href)
                                if parsed_next.netloc == base_domain:
                                    clean_next = next_href.split("#")[0].rstrip("/")
                                    if clean_next not in visited:
                                        queue.append((next_href, depth + 1))

                except Exception as crawl_err:  # noqa: BLE001
                    blocked_routes[clean_url] = f"Navigation error: {crawl_err}"
                    logger.debug("Failed to crawl route %s: %s", clean_url, crawl_err)

        # De-duplicate features by route, type, and name
        unique_features: list[DiscoveredFeature] = []
        seen_keys: set[str] = set()
        for f in features:
            key = f"{f.url}:{f.type}:{f.name}"
            if key not in seen_keys:
                seen_keys.add(key)
                unique_features.append(f)

        logger.info(
            "Crawl complete. Discovered %d routes, %d unique features, %d blocked routes.",
            len(routes),
            len(unique_features),
            len(blocked_routes),
        )

        return FeatureRegistry(
            base_url=start_url,
            routes=routes,
            features=unique_features,
            pages=pages,
            blocked_routes=blocked_routes,
        )
