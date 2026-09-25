"""Browser-isolation option propagation for site inspection."""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock

import pytest

import aiqa.planner.site_inspector as site_inspector_module
from aiqa.planner.site_inspector import InspectedPage, SiteInspector


class CapturingBrowserSession:
    created_with: list[dict[str, Any]] = []

    def __init__(self, **options: Any) -> None:
        self.created_with.append(options)
        self.page = AsyncMock()

    async def __aenter__(self) -> CapturingBrowserSession:
        return self

    async def __aexit__(self, *_args: Any) -> None:
        pass

    async def goto(self, _url: str) -> None:
        pass


@pytest.mark.asyncio
async def test_inspector_forwards_storage_state_to_browser_session(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    CapturingBrowserSession.created_with.clear()
    monkeypatch.setattr(
        site_inspector_module,
        "BrowserSession",
        CapturingBrowserSession,
    )
    expected = InspectedPage(url="https://example.test", title="Example")
    inspector = SiteInspector(storage_state=tmp_path / "auth.json")
    inspector.inspect_page = AsyncMock(return_value=expected)  # type: ignore[method-assign]

    result = await inspector.inspect("https://example.test")

    assert result is expected
    assert CapturingBrowserSession.created_with == [
        {
            "headless": True,
            "cdp_url": None,
            "timeout": 25000,
            "storage_state": tmp_path / "auth.json",
            "reuse_existing_context": False,
            "reuse_existing_page": False,
        }
    ]


@pytest.mark.asyncio
async def test_inspector_forwards_explicit_cdp_reuse_options(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    CapturingBrowserSession.created_with.clear()
    monkeypatch.setattr(
        site_inspector_module,
        "BrowserSession",
        CapturingBrowserSession,
    )
    inspector = SiteInspector(
        cdp_url="http://127.0.0.1:9222",
        reuse_existing_context=True,
        reuse_existing_page=True,
    )
    inspector.inspect_page = AsyncMock(  # type: ignore[method-assign]
        return_value=InspectedPage(url="https://example.test", title="Example")
    )

    await inspector.inspect("https://example.test")

    options = CapturingBrowserSession.created_with[0]
    assert options["reuse_existing_context"] is True
    assert options["reuse_existing_page"] is True

