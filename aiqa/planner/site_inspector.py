"""Website inspection and DOM structure extraction for AI test planning."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlparse

from aiqa.executor.browser_session import BrowserSession

logger = logging.getLogger(__name__)


@dataclass
class InspectedPage:
    """Structured inspection summary of a web page."""

    url: str
    title: str
    headings: list[str] = field(default_factory=list)
    nav_links: list[dict[str, str]] = field(default_factory=list)
    buttons: list[dict[str, str]] = field(default_factory=list)
    forms: list[dict[str, Any]] = field(default_factory=list)
    search_inputs: list[dict[str, str]] = field(default_factory=list)
    interactive_summary: str = ""

    def to_prompt_context(self) -> str:
        """Format inspected page structure into concise text context for an LLM prompt."""
        lines = [
            f"Page URL: {self.url}",
            f"Page Title: {self.title}",
        ]
        if self.headings:
            lines.append("Key Headings: " + " | ".join(self.headings[:8]))

        if self.search_inputs:
            lines.append("Search Inputs:")
            for s in self.search_inputs[:3]:
                lines.append(f"  - Selector: {s.get('selector', '')} (placeholder: '{s.get('placeholder', '')}', name: '{s.get('name', '')}')")

        if self.forms:
            lines.append("Forms:")
            for f in self.forms[:4]:
                inputs_str = ", ".join(
                    f"{inp.get('name') or inp.get('type') or 'input'}[{inp.get('type', 'text')}]"
                    for inp in f.get("inputs", [])[:6]
                )
                lines.append(f"  - Form action='{f.get('action', '')}' method='{f.get('method', 'GET')}': {inputs_str}")

        if self.buttons:
            btn_texts = [b.get("text", "") for b in self.buttons if b.get("text")]
            lines.append("Key Buttons: " + ", ".join(btn_texts[:12]))

        if self.nav_links:
            link_texts = [f"{l.get('text', '')} ({l.get('href', '')})" for l in self.nav_links if l.get("text")]
            lines.append("Main Navigation Links:\n  " + "\n  ".join(link_texts[:10]))

        return "\n".join(lines)


class SiteInspector:
    """Inspects a web page using BrowserSession to extract testable features."""

    def __init__(
        self,
        headless: bool = True,
        cdp_url: str | None = None,
        timeout: int = 25000,
        storage_state: Path | str | dict[str, Any] | None = None,
        reuse_existing_context: bool = False,
        reuse_existing_page: bool = False,
    ) -> None:
        self.headless = headless
        self.cdp_url = cdp_url
        self.timeout = timeout
        self.storage_state = storage_state
        self.reuse_existing_context = reuse_existing_context
        self.reuse_existing_page = reuse_existing_page

    async def inspect(self, url: str) -> InspectedPage:
        """Navigate to URL and extract page metadata and interactive elements."""
        async with BrowserSession(
            headless=self.headless,
            cdp_url=self.cdp_url,
            timeout=self.timeout,
            storage_state=self.storage_state,
            reuse_existing_context=self.reuse_existing_context,
            reuse_existing_page=self.reuse_existing_page,
        ) as session:
            await session.goto(url)
            # Give dynamic elements a moment to render
            try:
                await session.page.wait_for_load_state("domcontentloaded", timeout=5000)
            except Exception:
                pass

            return await self.inspect_page(session)

    async def inspect_page(self, session: BrowserSession) -> InspectedPage:
        """Extract structured elements from the currently active browser page."""
        page = session.page
        url = page.url
        title = await page.title()

        data: dict[str, Any] = await session.evaluate_js("""
            () => {
                const baseHref = window.location.origin;

                // Headings
                const headings = Array.from(document.querySelectorAll('h1, h2, h3'))
                    .map(h => (h.innerText || '').trim())
                    .filter(t => t.length > 0 && t.length < 80)
                    .slice(0, 15);

                // Search inputs
                const searchInputs = [];
                const inputs = Array.from(document.querySelectorAll('input'));
                for (const inp of inputs) {
                    const type = (inp.type || 'text').toLowerCase();
                    const name = (inp.name || '').toLowerCase();
                    const placeholder = inp.placeholder || '';
                    const id = inp.id || '';
                    if (
                        type === 'search' ||
                        name.includes('search') ||
                        name.includes('query') ||
                        name.includes('keyword') ||
                        placeholder.toLowerCase().includes('search') ||
                        id.toLowerCase().includes('search')
                    ) {
                        let selector = '';
                        if (id) selector = '#' + id;
                        else if (inp.name) selector = `input[name="${inp.name}"]`;
                        else selector = `input[placeholder="${placeholder}"]`;

                        searchInputs.push({
                            selector,
                            name: inp.name || '',
                            placeholder,
                            id
                        });
                    }
                }

                // Buttons
                const buttons = [];
                const btnElements = Array.from(document.querySelectorAll('button, input[type="submit"], input[type="button"], a.btn, a.button, [role="button"]'));
                for (const btn of btnElements) {
                    const text = (btn.innerText || btn.value || btn.getAttribute('aria-label') || '').trim();
                    if (text && text.length < 50) {
                        let selector = '';
                        if (btn.id) selector = '#' + btn.id;
                        else if (btn.getAttribute('name')) selector = `${btn.tagName.toLowerCase()}[name="${btn.getAttribute('name')}"]`;
                        else selector = `${btn.tagName.toLowerCase()}:has-text("${text.replace(/"/g, '\\"')}")`;

                        buttons.push({
                            text,
                            selector,
                            tag: btn.tagName.toLowerCase()
                        });
                    }
                }

                // Forms
                const forms = [];
                const formElements = Array.from(document.querySelectorAll('form'));
                for (const form of formElements) {
                    const formInputs = Array.from(form.querySelectorAll('input, select, textarea')).map(i => ({
                        type: i.type || 'text',
                        name: i.name || '',
                        id: i.id || '',
                        placeholder: i.placeholder || ''
                    }));
                    forms.push({
                        action: form.getAttribute('action') || '',
                        method: (form.getAttribute('method') || 'GET').toUpperCase(),
                        inputs: formInputs
                    });
                }

                // Navigation links (same-origin or relative)
                const navLinks = [];
                const links = Array.from(document.querySelectorAll('nav a, header a, a[href]'));
                const seenHrefs = new Set();
                for (const a of links) {
                    const text = (a.innerText || a.getAttribute('aria-label') || '').trim();
                    const href = a.getAttribute('href') || '';
                    if (text && href && !href.startsWith('#') && !href.startsWith('javascript:')) {
                        const fullHref = a.href || href;
                        if (!seenHrefs.has(fullHref)) {
                            seenHrefs.add(fullHref);
                            navLinks.push({ text, href: fullHref });
                        }
                    }
                }

                return {
                    headings,
                    searchInputs,
                    buttons: buttons.slice(0, 25),
                    forms: forms.slice(0, 10),
                    navLinks: navLinks.slice(0, 25)
                };
            }
        """)

        return InspectedPage(
            url=url,
            title=title,
            headings=data.get("headings", []),
            nav_links=data.get("navLinks", []),
            buttons=data.get("buttons", []),
            forms=data.get("forms", []),
            search_inputs=data.get("searchInputs", []),
        )
