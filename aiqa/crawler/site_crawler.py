"""Multi-route crawler for discovering site architecture and building a FeatureRegistry."""

from __future__ import annotations

import logging
from collections import deque
from urllib.parse import urljoin, urlparse

from aiqa.executor.browser_session import BrowserSession
from aiqa.models.coverage import DiscoveredFeature, FeatureRegistry

logger = logging.getLogger(__name__)


class SiteCrawler:
    """Explores web application routes and maps out testable features."""

    def __init__(
        self,
        headless: bool = True,
        cdp_url: str | None = None,
        max_pages: int = 5,
        max_depth: int = 2,
        timeout: int = 20000,
    ) -> None:
        self.headless = headless
        self.cdp_url = cdp_url
        self.max_pages = max_pages
        self.max_depth = max_depth
        self.timeout = timeout

    async def crawl(self, start_url: str) -> FeatureRegistry:
        """Crawl site starting from start_url and construct FeatureRegistry."""
        logger.info("Starting multi-route crawl from %s (max_pages=%d)", start_url, self.max_pages)
        parsed_start = urlparse(start_url)
        base_domain = parsed_start.netloc

        visited: set[str] = set()
        queue: deque[tuple[str, int]] = deque([(start_url, 0)])

        features: list[DiscoveredFeature] = []
        routes: list[str] = []
        feature_id_counts: dict[str, int] = {}

        def _get_unique_id(prefix: str) -> str:
            count = feature_id_counts.get(prefix, 0) + 1
            feature_id_counts[prefix] = count
            return f"{prefix}_{count:03d}"

        async with BrowserSession(
            headless=self.headless,
            cdp_url=self.cdp_url,
            timeout=self.timeout,
        ) as session:
            while queue and len(visited) < self.max_pages:
                current_url, depth = queue.popleft()
                clean_url = current_url.split("#")[0].rstrip("/")
                if clean_url in visited:
                    continue

                visited.add(clean_url)
                routes.append(clean_url)

                try:
                    logger.debug("Crawling route: %s (depth=%d)", clean_url, depth)
                    await session.goto(clean_url)
                    try:
                        await session.page.wait_for_load_state("domcontentloaded", timeout=4000)
                    except Exception:
                        pass

                    page_title = await session.page.title()

                    # Extract interactive features on this page
                    page_data = await session.evaluate_js("""
                        () => {
                            const internalLinks = [];
                            const forms = [];
                            const buttons = [];
                            const inputs = [];

                            // Extract internal links
                            const anchors = document.querySelectorAll('a[href]');
                            for (const a of Array.from(anchors).slice(0, 50)) {
                                const text = (a.innerText || a.getAttribute('aria-label') || '').trim();
                                const href = a.getAttribute('href') || '';
                                if (href && !href.startsWith('#') && !href.startsWith('javascript:')) {
                                    internalLinks.push({ text: text.substring(0, 50), href: a.href });
                                }
                            }

                            // Extract inputs
                            const inpElements = document.querySelectorAll('input, select, textarea');
                            for (const inp of Array.from(inpElements).slice(0, 20)) {
                                const type = (inp.type || 'text').toLowerCase();
                                const name = inp.name || '';
                                const placeholder = inp.placeholder || '';
                                const id = inp.id ? '#' + inp.id : '';
                                inputs.push({ type, name, placeholder, id });
                            }

                            // Extract buttons
                            const btnElements = document.querySelectorAll('button, input[type="submit"], [role="button"]');
                            for (const btn of Array.from(btnElements).slice(0, 20)) {
                                const text = (btn.innerText || btn.value || btn.getAttribute('aria-label') || '').trim();
                                if (text) {
                                    const id = btn.id ? '#' + btn.id : '';
                                    buttons.push({ text: text.substring(0, 50), id });
                                }
                            }

                            return { internalLinks, inputs, buttons };
                        }
                    """)

                    # 1. Classify Search Features
                    for inp in page_data.get("inputs", []):
                        name_lower = (inp.get("name") or "").lower()
                        type_lower = (inp.get("type") or "").lower()
                        place_lower = (inp.get("placeholder") or "").lower()
                        if "search" in name_lower or "search" in place_lower or type_lower == "search" or "query" in name_lower:
                            sel = inp.get("id") or (f"input[name='{inp.get('name')}']" if inp.get("name") else "input[type='search']")
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

                    # 2. Classify Cart & Checkout Features
                    for link in page_data.get("internalLinks", []):
                        l_text = link.get("text", "").lower()
                        l_href = link.get("href", "").lower()
                        if "cart" in l_text or "cart" in l_href or "basket" in l_text:
                            features.append(
                                DiscoveredFeature(
                                    id=_get_unique_id("feat_cart"),
                                    name=f"Shopping Cart: '{link.get('text') or 'Cart'}'",
                                    type="cart",
                                    url=clean_url,
                                    selector=f"a[href*='{l_href.split('/')[-1]}']" if l_href else None,
                                    description="View items currently in shopping cart",
                                )
                            )
                        elif "checkout" in l_text or "checkout" in l_href:
                            features.append(
                                DiscoveredFeature(
                                    id=_get_unique_id("feat_checkout"),
                                    name=f"Checkout Flow: '{link.get('text') or 'Checkout'}'",
                                    type="checkout",
                                    url=clean_url,
                                    description="Order checkout and payment gateway flow",
                                )
                            )
                        elif "login" in l_text or "sign in" in l_text or "account" in l_text:
                            features.append(
                                DiscoveredFeature(
                                    id=_get_unique_id("feat_auth"),
                                    name=f"Authentication: '{link.get('text') or 'Login'}'",
                                    type="auth",
                                    url=clean_url,
                                    description="User authentication, sign-in or account portal",
                                )
                            )

                    # 3. Classify Buttons
                    for btn in page_data.get("buttons", []):
                        b_text = btn.get("text", "")
                        b_text_lower = b_text.lower()
                        if "cart" in b_text_lower:
                            features.append(
                                DiscoveredFeature(
                                    id=_get_unique_id("feat_cart_action"),
                                    name=f"Add to Cart Action: '{b_text}'",
                                    type="cart",
                                    url=clean_url,
                                    selector=btn.get("id"),
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
                                    selector=btn.get("id"),
                                    description=f"Clickable action button '{b_text}'",
                                )
                            )

                    # 4. Enqueue internal links for subsequent depth crawling
                    if depth < self.max_depth:
                        for l in page_data.get("internalLinks", []):
                            next_href = l.get("href")
                            if next_href:
                                parsed_next = urlparse(next_href)
                                if parsed_next.netloc == base_domain and next_href not in visited:
                                    queue.append((next_href, depth + 1))

                except Exception as crawl_err:
                    logger.debug("Failed to crawl route %s: %s", clean_url, crawl_err)

        # De-duplicate features by name & type
        unique_features: list[DiscoveredFeature] = []
        seen_keys: set[str] = set()
        for f in features:
            key = f"{f.type}:{f.name}"
            if key not in seen_keys:
                seen_keys.add(key)
                unique_features.append(f)

        logger.info(
            "Crawl complete. Discovered %d routes, %d unique features.",
            len(routes),
            len(unique_features),
        )

        return FeatureRegistry(
            base_url=start_url,
            routes=routes,
            features=unique_features,
        )
