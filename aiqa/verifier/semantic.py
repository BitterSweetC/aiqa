"""Semantic LLM-based verification for AIQA test execution.

This module provides visual and semantic expectation verification using OpenAI
models (e.g. GPT-4o). It evaluates page state dictionaries and screenshots to
judge whether complex, visual, or non-deterministic test assertions hold true.
"""

from __future__ import annotations

import base64
import inspect
import json
import logging
import os
from pathlib import Path
from typing import Any

from aiqa.models.test_case import Expectation, VerificationResult

logger = logging.getLogger(__name__)


def _unwrap_page(page: Any) -> Any:
    """Extract underlying Playwright Page if a BrowserSession is passed."""
    if page is None:
        return None
    prop = getattr(type(page), "page", None)
    if isinstance(prop, property):
        try:
            return page.page
        except Exception:  # noqa: BLE001, S110
            pass
    return page


class SemanticVerifier:
    """Uses an LLM to verify test expectations by analyzing screenshots/page state."""

    def __init__(
        self,
        api_key: str | None = None,
        model: str = "gpt-4o",
        **kwargs: Any,
    ) -> None:
        """Initialize the semantic LLM verifier.

        Args:
            api_key: OpenAI API key. If omitted, falls back to OPENAI_API_KEY env var.
            model: OpenAI chat completion model name (default: "gpt-4o").
            **kwargs: Extra arguments (e.g. openai_api_key alias).
        """
        self.api_key = api_key or kwargs.get("openai_api_key") or os.environ.get("OPENAI_API_KEY")
        self.model = model
        self._client: Any = None

    def _get_client(self) -> Any:
        """Lazily initialize and return the AsyncOpenAI client instance.

        Returns:
            AsyncOpenAI client or None if openai is not installed.
        """
        if self._client is not None:
            return self._client

        try:
            from openai import AsyncOpenAI

            self._client = AsyncOpenAI(api_key=self.api_key)
            return self._client
        except ImportError:
            return None

    async def verify(
        self,
        expectation: Expectation,
        page_state: dict[str, Any] | Any = None,
        screenshot_path: str | Path | None = None,
        *,
        page: Any = None,
        session: Any = None,
        **kwargs: Any,
    ) -> VerificationResult:
        """Ask the LLM: Given this page state and screenshot, does the expectation hold?

        Returns a VerificationResult with LLM's judgment.

        Args:
            expectation: The test expectation to verify.
            page_state: Dictionary containing page URL, title, DOM/text state,
                or a Playwright Page / session instance.
            screenshot_path: Optional path to screenshot file captured for the page.
            page: Optional Playwright Page instance (passed by some callers).
            session: Optional BrowserSession instance.
            **kwargs: Extra keyword arguments.

        Returns:
            VerificationResult containing passed status, actual value summary,
            and explanation.
        """
        # Ensure arguments are in expected order even if inverted by caller
        if not isinstance(expectation, Expectation):
            if isinstance(page_state, Expectation):
                expectation, page_state = page_state, expectation
            elif isinstance(page, Expectation):
                expectation, page = page, expectation

        # Check for OpenAI API key
        resolved_key = self.api_key or os.environ.get("OPENAI_API_KEY")
        if not resolved_key:
            return VerificationResult(
                expectation=expectation,
                passed=False,
                actual_value=None,
                message=(
                    "LLM verification requires an API key. Set OPENAI_API_KEY "
                    "environment variable or pass api_key to SemanticVerifier."
                ),
            )

        # Ensure openai library is importable
        try:
            import openai  # noqa: F401
        except ImportError:
            return VerificationResult(
                expectation=expectation,
                passed=False,
                actual_value=None,
                message=(
                    "The 'openai' library is required for semantic verification. "
                    "Install it with: pip install 'openai>=1.0'"
                ),
            )

        # Normalize page_state dict from inputs
        state_dict = await self._normalize_page_state(
            page_state=page_state, page=page, session=session
        )

        # If screenshot_path is not specified, check if kwargs or session provides one
        if screenshot_path is None:
            screenshot_path = kwargs.get("screenshot") or kwargs.get("screenshot_file")
        if screenshot_path is None and isinstance(state_dict, dict):
            screenshot_path = state_dict.get("screenshot_path")

        # Encode screenshot if available and valid
        image_data_uri: str | None = None
        if screenshot_path:
            img_p = Path(screenshot_path)
            if img_p.is_file():
                try:
                    img_bytes = img_p.read_bytes()
                    b64_str = base64.b64encode(img_bytes).decode("utf-8")
                    suffix = img_p.suffix.lower().lstrip(".") or "png"
                    mime_type = "image/jpeg" if suffix in ("jpg", "jpeg") else "image/png"
                    image_data_uri = f"data:{mime_type};base64,{b64_str}"
                except Exception as exc:  # noqa: BLE001
                    logger.warning("Could not read screenshot at %s: %s", screenshot_path, exc)

        try:
            client = self._get_client()
            if client is None:
                from openai import AsyncOpenAI

                client = AsyncOpenAI(api_key=resolved_key)
                self._client = client

            system_prompt = (
                "You are an expert autonomous web QA verification engine. "
                "Your role is to verify whether a given test expectation holds true "
                "on the web page based on the provided page state (URL, title, visible text) "
                "and optional screenshot image.\n\n"
                "You must respond STRICTLY with a valid JSON object matching this schema:\n"
                "{\n"
                '  "passed": boolean,        // true if expectation is satisfied\n'
                '  "actual_value": string,   // concise summary of observed state\n'
                '  "reason": string          // clear, factual explanation\n'
                "}"
            )

            # Build user prompt
            user_text_parts = [
                f"## Test Expectation to Verify:\n{expectation.description}",
            ]
            if expectation.value:
                user_text_parts.append(f"Expected Value / Target: {expectation.value}")
            if expectation.selector:
                user_text_parts.append(f"Target Selector: {expectation.selector}")

            user_text_parts.append("\n## Current Page State:")
            user_text_parts.append(f"- URL: {state_dict.get('url', 'N/A')}")
            user_text_parts.append(f"- Title: {state_dict.get('title', 'N/A')}")

            # Include visible text (truncated to avoid context blowup)
            text_snippet = state_dict.get("text", "")
            if text_snippet:
                user_text_parts.append(f"\nVisible Text Content:\n{text_snippet[:4000]}")

            # Include HTML snippet if text is short or absent
            html_snippet = state_dict.get("html") or state_dict.get("dom", "")
            if html_snippet and len(text_snippet) < 200:
                user_text_parts.append(f"\nDOM Snapshot Snippet:\n{html_snippet[:4000]}")

            user_prompt_str = "\n".join(user_text_parts)

            # Assemble multimodal content
            user_content: list[dict[str, Any]] = [
                {"type": "text", "text": user_prompt_str},
            ]
            if image_data_uri:
                user_content.append(
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": image_data_uri,
                            "detail": "auto",
                        },
                    }
                )

            messages = [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_content},
            ]

            response = await client.chat.completions.create(
                model=self.model,
                messages=messages,
                response_format={"type": "json_object"},
                temperature=0.0,
            )

            response_raw = response.choices[0].message.content or "{}"
            result_data = json.loads(response_raw)

            passed = bool(result_data.get("passed", False))
            actual_value = str(result_data.get("actual_value", ""))
            reason = str(result_data.get("reason", ""))

            return VerificationResult(
                expectation=expectation,
                passed=passed,
                actual_value=actual_value or None,
                message=reason or f"Semantic verification {'passed' if passed else 'failed'}",
            )

        except Exception as exc:
            logger.exception("Semantic verification execution error")
            return VerificationResult(
                expectation=expectation,
                passed=False,
                actual_value=None,
                message=f"Semantic verification error: {exc}",
            )

    async def _normalize_page_state(
        self,
        page_state: Any,
        page: Any = None,
        session: Any = None,
    ) -> dict[str, Any]:
        """Normalize page_state into a dictionary with url, title, and text."""
        if isinstance(page_state, dict):
            return page_state

        source = page_state or page or session
        if source is None:
            return {}

        state: dict[str, Any] = {}

        # If source has get_page_state coroutine (e.g. BrowserSession)
        if hasattr(source, "get_page_state") and callable(source.get_page_state):
            try:
                res = source.get_page_state()
                extracted = await res if inspect.isawaitable(res) else res
                if isinstance(extracted, dict):
                    return extracted
            except Exception:  # noqa: BLE001, S110
                pass

        page_obj = _unwrap_page(source)

        # Extract URL
        if hasattr(page_obj, "url"):
            url_prop = page_obj.url
            if callable(url_prop):
                res = url_prop()
                state["url"] = await res if inspect.isawaitable(res) else str(res or "")
            else:
                state["url"] = str(url_prop or "")

        # Extract Title
        if hasattr(page_obj, "title"):
            try:
                title_prop = page_obj.title() if callable(page_obj.title) else page_obj.title
                state["title"] = (
                    await title_prop if inspect.isawaitable(title_prop) else str(title_prop or "")
                )
            except Exception:  # noqa: BLE001, S110
                pass

        # Extract Text
        if hasattr(page_obj, "inner_text"):
            try:
                res = page_obj.inner_text("body")
                state["text"] = await res if inspect.isawaitable(res) else str(res or "")
            except Exception:  # noqa: BLE001, S110
                pass

        # Extract HTML
        if hasattr(page_obj, "content"):
            try:
                res = page_obj.content()
                state["html"] = await res if inspect.isawaitable(res) else str(res or "")
            except Exception:  # noqa: BLE001, S110
                pass

        return state


__all__ = ["SemanticVerifier"]
