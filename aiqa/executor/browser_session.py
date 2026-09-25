"""Browser session management using Playwright for AIQA test execution."""

from __future__ import annotations

import logging
from pathlib import Path
from types import TracebackType
from typing import Any, Self

logger = logging.getLogger(__name__)

# Safe Playwright import to allow imports and static analysis even if
# Playwright is not yet installed in the target environment.
try:
    from playwright.async_api import (
        Browser,
        BrowserContext,
        Page,
        Playwright,
        async_playwright,
    )
    from playwright.async_api import (
        Error as PlaywrightError,
    )

    PLAYWRIGHT_AVAILABLE = True
except ImportError:  # pragma: no cover
    Browser = Any  # type: ignore[misc,assignment]
    BrowserContext = Any  # type: ignore[misc,assignment]
    Page = Any  # type: ignore[misc,assignment]
    Playwright = Any  # type: ignore[misc,assignment]
    PlaywrightError = Exception  # type: ignore[misc,assignment]
    async_playwright = None  # type: ignore[assignment]
    PLAYWRIGHT_AVAILABLE = False


class BrowserSession:
    """Manages a Playwright browser instance for test execution.

    Provides an asynchronous context manager interface to safely launch,
    navigate, inspect, and cleanly shut down a headless or headed Playwright
    Chromium browser context and page.

    Example:
        async with BrowserSession(headless=True, timeout=30000) as session:
            await session.goto("http://localhost:3000")
            state = await session.get_page_state()
            screenshot_path = await session.screenshot("reports/screenshot.png")
    """

    def __init__(
        self,
        headless: bool = True,
        timeout: int = 30000,
        viewport: dict[str, int] | None = None,
        cdp_url: str | None = None,
        user_data_dir: Path | str | None = None,
        storage_state: Path | str | dict[str, Any] | None = None,
        reuse_existing_context: bool = False,
        reuse_existing_page: bool = False,
        is_mobile: bool = False,
        user_agent: str | None = None,
        downloads_dir: Path | str | None = None,
    ) -> None:
        """Initialize the browser session configuration."""
        if reuse_existing_page and not reuse_existing_context:
            raise ValueError("reuse_existing_page requires reuse_existing_context")
        if (reuse_existing_context or reuse_existing_page) and not cdp_url:
            raise ValueError("Browser context/page reuse requires a CDP URL")
        if storage_state is not None and reuse_existing_context:
            raise ValueError(
                "storage_state cannot be loaded when reusing an existing CDP context"
            )
        if storage_state is not None and user_data_dir is not None:
            raise ValueError("storage_state cannot be combined with user_data_dir")

        self.headless = headless
        self.timeout = timeout
        self.is_mobile = is_mobile
        self.user_agent = user_agent
        if viewport is not None:
            self.viewport = viewport
        elif is_mobile:
            self.viewport = {"width": 390, "height": 844}
        else:
            self.viewport = {"width": 1280, "height": 720}
        self.cdp_url = cdp_url
        self.user_data_dir = Path(user_data_dir) if user_data_dir is not None else None
        self.storage_state = (
            str(storage_state) if isinstance(storage_state, Path) else storage_state
        )
        self.reuse_existing_context = reuse_existing_context
        self.reuse_existing_page = reuse_existing_page
        self.downloads_dir = Path(downloads_dir) if downloads_dir is not None else None

        self._playwright: Playwright | None = None
        self._browser: Browser | None = None
        self._context: BrowserContext | None = None
        self._page: Page | None = None
        self._is_cdp: bool = False
        self._is_persistent: bool = False
        self._owns_browser: bool = False
        self._owns_context: bool = False
        self._owns_page: bool = False

        self.console_logs: list[dict[str, Any]] = []
        self.network_errors: list[dict[str, Any]] = []
        self.downloads: list[dict[str, Any]] = []

    def _build_context_options(self) -> dict[str, Any]:
        opts: dict[str, Any] = {
            "viewport": self.viewport,
        }
        if self.storage_state is not None:
            opts["storage_state"] = self.storage_state
        if self.is_mobile:
            opts["is_mobile"] = True
            opts["has_touch"] = True
        if self.user_agent:
            opts["user_agent"] = self.user_agent
        return opts

    async def __aenter__(self) -> Self:
        """Launch Playwright, create browser context and page."""
        await self.start()
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None = None,
        exc_val: BaseException | None = None,
        exc_tb: TracebackType | None = None,
    ) -> None:
        """Clean up browser, context, and Playwright driver resources."""
        await self.close()

    async def start(self) -> Self:
        """Explicitly launch Playwright and browser resources."""
        if not PLAYWRIGHT_AVAILABLE or async_playwright is None:
            raise RuntimeError(
                "Playwright is not installed. Please install it using: "
                "pip install 'playwright>=1.40' && playwright install chromium"
            )

        if self._page is not None and not self._page.is_closed():
            logger.debug("BrowserSession is already active.")
            return self

        try:
            self._playwright = await async_playwright().start()

            # Mode 1: Connect to existing browser over CDP (e.g. port 9222)
            if self.cdp_url:
                logger.info("Connecting to existing browser over CDP: %s", self.cdp_url)
                self._is_cdp = True
                self._browser = await self._playwright.chromium.connect_over_cdp(
                    self.cdp_url,
                    timeout=self.timeout,
                )

                if self.reuse_existing_context and self._browser.contexts:
                    self._context = self._browser.contexts[0]
                else:
                    context_options = self._build_context_options()
                    self._context = await self._browser.new_context(**context_options)
                    self._owns_context = True

                if self.reuse_existing_page and self._context.pages:
                    self._page = self._context.pages[0]
                else:
                    self._page = await self._context.new_page()
                    self._owns_page = True

                self._context.set_default_timeout(self.timeout)
                self._page.set_default_timeout(self.timeout)
                self._attach_telemetry_listeners(self._page)
                logger.debug("BrowserSession attached via CDP successfully.")
                return self

            # Mode 2: Persistent context with user data dir (cookies/login)
            if self.user_data_dir:
                logger.info(
                    "Launching persistent context with profile: %s (headless=%s)",
                    self.user_data_dir,
                    self.headless,
                )
                self._is_persistent = True
                self._context = await self._playwright.chromium.launch_persistent_context(
                    user_data_dir=str(self.user_data_dir),
                    headless=self.headless,
                    viewport=self.viewport,
                    args=[
                        "--no-sandbox",
                        "--disable-dev-shm-usage",
                        "--disable-blink-features=AutomationControlled",
                    ],
                    ignore_default_args=["--enable-automation"],
                )
                self._owns_context = True
                self._browser = (
                    self._context.browser
                    if hasattr(self._context, "browser") and self._context.browser is not None
                    else None
                )
                if self._context.pages:
                    self._page = self._context.pages[0]
                else:
                    self._page = await self._context.new_page()
                self._owns_page = True

                self._context.set_default_timeout(self.timeout)
                self._page.set_default_timeout(self.timeout)
                self._attach_telemetry_listeners(self._page)
                logger.debug("BrowserSession persistent context started successfully.")
                return self

            # Mode 3: Standard ephemeral Chromium launch
            logger.debug(
                "Launching Playwright Chromium browser (headless=%s, timeout=%dms)",
                self.headless,
                self.timeout,
            )
            self._browser = await self._playwright.chromium.launch(
                headless=self.headless,
                args=[
                    "--no-sandbox",
                    "--disable-dev-shm-usage",
                    "--disable-gpu",
                ],
            )
            self._owns_browser = True
            context_options = self._build_context_options()
            self._context = await self._browser.new_context(**context_options)
            self._owns_context = True
            self._context.set_default_timeout(self.timeout)

            self._page = await self._context.new_page()
            self._owns_page = True
            self._page.set_default_timeout(self.timeout)
            self._attach_telemetry_listeners(self._page)
            logger.debug("BrowserSession successfully started.")
            return self
        except Exception as exc:
            logger.error("Failed to launch browser session: %s", exc)
            await self.close()
            raise

    async def close(self) -> None:
        """Safely close page, context, browser, and Playwright driver."""
        logger.debug("Closing browser session resources...")

        if self._page is not None and self._owns_page:
            try:
                if not self._page.is_closed():
                    await self._page.close()
            except (PlaywrightError, Exception) as exc:  # noqa: BLE001
                logger.debug("Error closing page: %s", exc)
            finally:
                self._owns_page = False

        if self._context is not None and self._owns_context:
            try:
                await self._context.close()
            except (PlaywrightError, Exception) as exc:  # noqa: BLE001
                logger.debug("Error closing context: %s", exc)
            finally:
                self._owns_context = False

        self._page = None
        self._context = None

        if self._browser is not None and self._owns_browser:
            try:
                if self._browser.is_connected():
                    await self._browser.close()
            except (PlaywrightError, Exception) as exc:  # noqa: BLE001
                logger.debug("Error closing browser: %s", exc)
            finally:
                self._owns_browser = False

        self._browser = None

        if self._playwright is not None:
            try:
                await self._playwright.stop()
            except (PlaywrightError, Exception) as exc:  # noqa: BLE001
                logger.debug("Error stopping Playwright driver: %s", exc)
            finally:
                self._playwright = None

    @property
    def page(self) -> Page:
        """Return the active Playwright Page instance.

        Raises:
            RuntimeError: If browser session is not active.
        """
        if self._page is None or self._page.is_closed():
            raise RuntimeError(
                "Browser session is not active. Use 'async with BrowserSession():' "
                "or 'await session.start()' before accessing page."
            )
        return self._page

    @property
    def browser(self) -> Browser:
        """Return the active Playwright Browser instance.

        Raises:
            RuntimeError: If browser is not connected or active.
        """
        if self._browser is None or not self._browser.is_connected():
            raise RuntimeError("Browser is not connected or active.")
        return self._browser

    @property
    def context(self) -> BrowserContext:
        """Return the active Playwright BrowserContext instance.

        Raises:
            RuntimeError: If browser context is not active.
        """
        if self._context is None:
            raise RuntimeError("Browser context is not active.")
        return self._context

    @property
    def is_active(self) -> bool:
        """Return True if session has an active, unclosed page."""
        return self._page is not None and not self._page.is_closed()

    async def goto(self, url: str) -> None:
        """Navigate to the specified URL and wait for page load.

        Args:
            url: Target web URL (e.g., 'http://localhost:3000' or 'https://example.com').
        """
        # Automatically prepend scheme if missing
        if not ("://" in url or url.startswith(("about:", "data:", "javascript:"))):
            if url.startswith(("localhost", "127.0.0.1")):
                url = f"http://{url}"
            else:
                url = f"https://{url}"

        logger.info("Navigating to URL: %s", url)
        await self.page.goto(url, wait_until="load", timeout=self.timeout)
        try:
            has_ext_scripts = await self.page.evaluate(
                "() => document.querySelectorAll('script[src]').length > 0"
            )
            if has_ext_scripts and hasattr(self.page, "wait_for_timeout"):
                await self.page.wait_for_timeout(250)
        except Exception as exc:  # noqa: BLE001
            logger.debug("Hydration wait skipped: %s", exc)

    async def screenshot(self, path: str | Path) -> str:
        """Capture a full-page screenshot and save to the specified path.

        Automatically ensures that destination parent directories exist.

        Args:
            path: Destination path for the screenshot image file (.png).

        Returns:
            The resolved string path to the saved screenshot.
        """
        file_path = Path(path).resolve()
        file_path.parent.mkdir(parents=True, exist_ok=True)

        logger.info("Saving screenshot to %s", file_path)
        await self.page.screenshot(path=str(file_path), full_page=True)
        return str(file_path)

    async def get_page_state(self) -> dict[str, Any]:
        """Capture current page state including URL, title, and DOM snapshot.

        Returns:
            A dictionary containing:
                - url (str): Current page URL.
                - title (str): Current document title.
                - html (str): Full HTML DOM snapshot.
                - dom (str): Full HTML snapshot (alias for html).
                - text (str): Visible body text content (if available).
        """
        title = ""
        html = ""
        for attempt in range(4):
            try:
                url = self.page.url
                title = await self.page.title()
                html = await self.page.content()
                break
            except (PlaywrightError, Exception) as exc:
                if attempt == 3:
                    raise
                logger.debug("Retrying get_page_state during navigation (%s)...", exc)
                if hasattr(self.page, "wait_for_timeout"):
                    await self.page.wait_for_timeout(150)

        url = self.page.url
        text_content = ""
        try:
            text_content = await self.page.inner_text("body")
        except (PlaywrightError, Exception) as exc:  # noqa: BLE001
            logger.debug("Failed to extract body text: %s", exc)

        return {
            "url": url,
            "title": title,
            "html": html,
            "dom": html,
            "text": text_content,
        }

    async def evaluate_js(self, script: str, *args: Any) -> Any:
        """Evaluate a JavaScript expression or function in the page context.

        Args:
            script: JavaScript snippet or function body to execute.
            *args: Optional serializable parameters passed into the script.

        Returns:
            The evaluation result returned by the page context.
        """
        logger.debug("Evaluating JS in page: %s", script[:100])
        return await self.page.evaluate(script, *args)

    def _attach_telemetry_listeners(self, page: Any) -> None:
        """Attach event listeners for console messages and network errors."""
        def on_console(msg: Any) -> None:
            try:
                msg_type = getattr(msg, "type", "")
                if msg_type in ("error", "warning"):
                    self.console_logs.append({
                        "type": msg_type,
                        "text": getattr(msg, "text", str(msg)),
                        "location": getattr(msg, "location", None),
                    })
            except Exception as err:  # noqa: BLE001
                logger.debug("Telemetry console listener error: %s", err)

        def on_page_error(err: Any) -> None:
            try:
                self.console_logs.append({
                    "type": "error",
                    "text": str(err),
                })
            except Exception as handler_err:  # noqa: BLE001
                logger.debug("Telemetry pageerror listener error: %s", handler_err)

        def on_response(res: Any) -> None:
            try:
                status = getattr(res, "status", 200)
                if status >= 400:
                    req = getattr(res, "request", None)
                    method = getattr(req, "method", "GET") if req else "GET"
                    self.network_errors.append({
                        "url": getattr(res, "url", ""),
                        "status": status,
                        "status_text": getattr(res, "status_text", ""),
                        "method": method,
                    })
            except Exception as err:  # noqa: BLE001
                logger.debug("Telemetry response listener error: %s", err)

        def on_request_failed(req: Any) -> None:
            try:
                self.network_errors.append({
                    "url": getattr(req, "url", ""),
                    "method": getattr(req, "method", "GET"),
                    "failure": str(getattr(req, "failure", "failed")),
                    "status": 0,
                })
            except Exception as err:  # noqa: BLE001
                logger.debug("Telemetry requestfailed listener error: %s", err)

        async def on_download(download: Any) -> None:
            try:
                suggested_filename = str(getattr(download, "suggested_filename", "download"))
                url = str(getattr(download, "url", ""))
                record: dict[str, Any] = {
                    "suggested_filename": suggested_filename,
                    "url": url,
                    "path": None,
                    "size_bytes": 0,
                    "_download": download,
                }
                self.downloads.append(record)
                if self.downloads_dir is not None:
                    self.downloads_dir.mkdir(parents=True, exist_ok=True)
                    dest = self.downloads_dir / suggested_filename
                    await download.save_as(str(dest))
                    record["path"] = str(dest)
                    if dest.exists():
                        record["size_bytes"] = dest.stat().st_size
                else:
                    dl_path = await download.path()
                    if dl_path:
                        p = Path(dl_path)
                        record["path"] = str(p)
                        if p.exists():
                            record["size_bytes"] = p.stat().st_size
            except Exception as err:  # noqa: BLE001
                logger.debug("Telemetry download listener error: %s", err)

        try:
            page.on("console", on_console)
            page.on("pageerror", on_page_error)
            page.on("response", on_response)
            page.on("requestfailed", on_request_failed)
            page.on("download", on_download)
        except Exception as err:  # noqa: BLE001
            logger.debug("Could not attach telemetry listeners: %s", err)

    def get_telemetry(self) -> dict[str, list[dict[str, Any]]]:
        """Retrieve recorded browser console logs and network errors."""
        return {
            "console_logs": list(self.console_logs),
            "network_errors": list(self.network_errors),
        }

    def clear_telemetry(self) -> None:
        """Reset telemetry buffers."""
        self.console_logs.clear()
        self.network_errors.clear()
        self.downloads.clear()
