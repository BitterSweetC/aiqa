"""Browser-state isolation and resource-ownership tests."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

import aiqa.executor.browser_session as browser_session_module
from aiqa.executor.browser_session import BrowserSession


class FakePage:
    def __init__(self) -> None:
        self.closed = False
        self.timeout: int | None = None

    def is_closed(self) -> bool:
        return self.closed

    async def close(self) -> None:
        self.closed = True

    def set_default_timeout(self, timeout: int) -> None:
        self.timeout = timeout

    def on(self, _event: str, _handler: Any) -> None:
        pass


class FakeContext:
    def __init__(self, pages: list[FakePage] | None = None) -> None:
        self.pages = list(pages or [])
        self.closed = False
        self.timeout: int | None = None
        self.new_page_calls = 0

    async def new_page(self) -> FakePage:
        self.new_page_calls += 1
        page = FakePage()
        self.pages.append(page)
        return page

    async def close(self) -> None:
        self.closed = True
        for page in self.pages:
            page.closed = True

    def set_default_timeout(self, timeout: int) -> None:
        self.timeout = timeout


class FakeBrowser:
    def __init__(self, contexts: list[FakeContext] | None = None) -> None:
        self.contexts = list(contexts or [])
        self.new_context_options: list[dict[str, Any]] = []
        self.close_calls = 0

    async def new_context(self, **options: Any) -> FakeContext:
        self.new_context_options.append(options)
        context = FakeContext()
        self.contexts.append(context)
        return context

    def is_connected(self) -> bool:
        return True

    async def close(self) -> None:
        self.close_calls += 1


class FakeChromium:
    def __init__(self, browser: FakeBrowser) -> None:
        self.browser = browser
        self.connect_options: dict[str, Any] | None = None
        self.launch_options: dict[str, Any] | None = None

    async def connect_over_cdp(self, url: str, **options: Any) -> FakeBrowser:
        self.connect_options = {"url": url, **options}
        return self.browser

    async def launch(self, **options: Any) -> FakeBrowser:
        self.launch_options = options
        return self.browser


class FakePlaywright:
    def __init__(self, browser: FakeBrowser) -> None:
        self.chromium = FakeChromium(browser)
        self.stop_calls = 0

    async def stop(self) -> None:
        self.stop_calls += 1


class FakePlaywrightStarter:
    def __init__(self, playwright: FakePlaywright) -> None:
        self.playwright = playwright

    async def start(self) -> FakePlaywright:
        return self.playwright


def install_fake_playwright(
    monkeypatch: pytest.MonkeyPatch,
    browser: FakeBrowser,
) -> FakePlaywright:
    playwright = FakePlaywright(browser)
    monkeypatch.setattr(browser_session_module, "PLAYWRIGHT_AVAILABLE", True)
    monkeypatch.setattr(
        browser_session_module,
        "async_playwright",
        lambda: FakePlaywrightStarter(playwright),
    )
    return playwright


@pytest.mark.asyncio
async def test_standard_session_loads_storage_state_into_isolated_context(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    browser = FakeBrowser()
    install_fake_playwright(monkeypatch, browser)
    storage_state = tmp_path / "auth.json"

    session = BrowserSession(storage_state=storage_state)
    await session.start()

    assert browser.new_context_options == [
        {
            "viewport": {"width": 1280, "height": 720},
            "storage_state": str(storage_state),
        }
    ]

    created_context = session.context
    created_page = session.page
    await session.close()

    assert created_context.closed is True
    assert created_page.closed is True
    assert browser.close_calls == 1


@pytest.mark.asyncio
async def test_cdp_default_creates_and_closes_its_own_context_and_page(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    user_page = FakePage()
    user_context = FakeContext([user_page])
    browser = FakeBrowser([user_context])
    install_fake_playwright(monkeypatch, browser)

    session = BrowserSession(cdp_url="http://127.0.0.1:9222")
    await session.start()

    created_context = session.context
    created_page = session.page
    assert created_context is not user_context
    assert created_page is not user_page

    await session.close()

    assert created_context.closed is True
    assert created_page.closed is True
    assert user_context.closed is False
    assert user_page.closed is False
    assert browser.close_calls == 0


@pytest.mark.asyncio
async def test_cdp_context_reuse_creates_and_closes_only_its_own_page(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    user_page = FakePage()
    user_context = FakeContext([user_page])
    browser = FakeBrowser([user_context])
    install_fake_playwright(monkeypatch, browser)

    session = BrowserSession(
        cdp_url="http://127.0.0.1:9222",
        reuse_existing_context=True,
    )
    await session.start()

    created_page = session.page
    assert session.context is user_context
    assert created_page is not user_page

    await session.close()

    assert created_page.closed is True
    assert user_context.closed is False
    assert user_page.closed is False
    assert browser.close_calls == 0


@pytest.mark.asyncio
async def test_cdp_page_reuse_is_explicit_and_preserves_user_resources(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    user_page = FakePage()
    user_context = FakeContext([user_page])
    browser = FakeBrowser([user_context])
    install_fake_playwright(monkeypatch, browser)

    session = BrowserSession(
        cdp_url="http://127.0.0.1:9222",
        reuse_existing_context=True,
        reuse_existing_page=True,
    )
    await session.start()

    assert session.context is user_context
    assert session.page is user_page
    await session.close()

    assert user_context.closed is False
    assert user_page.closed is False
    assert browser.close_calls == 0


def test_reusing_existing_page_requires_context_reuse() -> None:
    with pytest.raises(
        ValueError,
        match="reuse_existing_page requires reuse_existing_context",
    ):
        BrowserSession(cdp_url="http://127.0.0.1:9222", reuse_existing_page=True)


def test_cdp_reuse_options_require_cdp_url() -> None:
    with pytest.raises(ValueError, match="CDP URL"):
        BrowserSession(reuse_existing_context=True)


def test_storage_state_cannot_be_silently_ignored_by_context_reuse() -> None:
    with pytest.raises(ValueError, match="storage_state cannot be loaded"):
        BrowserSession(
            cdp_url="http://127.0.0.1:9222",
            storage_state={"cookies": [], "origins": []},
            reuse_existing_context=True,
        )


def test_storage_state_cannot_be_combined_with_persistent_profile(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="storage_state cannot be combined"):
        BrowserSession(
            user_data_dir=tmp_path / "profile",
            storage_state=tmp_path / "auth.json",
        )
