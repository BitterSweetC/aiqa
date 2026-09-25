"""Security and origin constraint policies for AIQA."""

from __future__ import annotations

import urllib.parse
from collections.abc import Iterable
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from aiqa.models.test_case import TestSuite

SAFE_URL_SCHEMES = {"http", "https", "about", "data"}
UNSAFE_URL_SCHEMES = {"javascript", "vbscript", "file"}


def extract_origin(url: str | None) -> str | None:
    """Extract normalized origin (scheme://netloc) from a URL.

    Returns None for relative paths or URLs without network location.
    """
    if not url:
        return None
    trimmed = url.strip()
    if trimmed.startswith(("about:", "data:")):
        return trimmed.split("?")[0].split("#")[0].lower()
    parsed = urllib.parse.urlparse(trimmed)
    if parsed.scheme.lower() in ("http", "https") and parsed.netloc:
        return f"{parsed.scheme.lower()}://{parsed.netloc.lower()}"
    return None


def is_safe_url_scheme(url: str | None) -> bool:
    """Return True if URL uses an approved browser scheme or is a relative path."""
    if not url:
        return False
    trimmed = url.strip()
    parsed = urllib.parse.urlparse(trimmed)
    if not parsed.scheme:
        # Relative path is safe
        return not trimmed.lower().startswith(("javascript:", "vbscript:", "file:"))
    return parsed.scheme.lower() in SAFE_URL_SCHEMES


def is_origin_allowed(
    url: str,
    allowed_origins: Iterable[str],
    allow_cross_origin: bool = False,
) -> bool:
    """Check if target URL belongs to configured allowed origins."""
    if allow_cross_origin:
        return True
    origin = extract_origin(url)
    if origin is None:
        # Relative URL or scheme without host (about:blank, data:...)
        return True

    normalized_allowed: set[str] = set()
    for o in allowed_origins:
        ext = extract_origin(o)
        if ext:
            normalized_allowed.add(ext)
        elif o.strip():
            normalized_allowed.add(o.strip().lower())

    return origin in normalized_allowed


def validate_test_suite_policy(suite: TestSuite) -> list[str]:
    """Validate suite configuration, schema, timeouts, and origins before execution.

    Returns a list of policy violation error messages (empty if valid).
    """
    errors: list[str] = []

    if not suite.base_url or not suite.base_url.strip():
        errors.append("Suite base_url must not be empty.")
    elif not is_safe_url_scheme(suite.base_url):
        errors.append(f"Suite base_url '{suite.base_url}' uses an unsafe or unsupported URL scheme.")

    # Validate unique test IDs
    seen_ids: set[str] = set()
    for test in suite.tests:
        if test.id in seen_ids:
            errors.append(f"Duplicate test ID '{test.id}' in suite '{suite.name}'.")
        seen_ids.add(test.id)

        # Validate timeout
        if not (1 <= test.timeout <= 600):
            errors.append(f"Test '{test.id}' timeout {test.timeout}s outside valid range [1, 600].")

        # Validate start_url
        if not test.start_url or not test.start_url.strip():
            errors.append(f"Test '{test.id}' start_url must not be empty.")
        elif not is_safe_url_scheme(test.start_url):
            errors.append(f"Test '{test.id}' start_url '{test.start_url}' uses an unsafe URL scheme.")

        # Validate actions
        if test.actions is not None:
            for action in test.actions:
                if action.action == "navigate":
                    nav_url = getattr(action, "url", "")
                    if not is_safe_url_scheme(nav_url):
                        errors.append(
                            f"Test '{test.id}' navigate action uses unsafe URL '{nav_url}'."
                        )

    # Origin constraints
    if not suite.allow_cross_origin:
        allowed = [suite.base_url] + suite.allowed_origins
        for test in suite.tests:
            if not is_origin_allowed(test.start_url, allowed, allow_cross_origin=False):
                errors.append(
                    f"Test '{test.id}' start_url '{test.start_url}' violates origin policy. "
                    f"Allowed origins: {allowed}. Set allow_cross_origin=True to permit."
                )
            if test.actions is not None:
                for action in test.actions:
                    if action.action == "navigate":
                        nav_url = getattr(action, "url", "")
                        if not is_origin_allowed(nav_url, allowed, allow_cross_origin=False):
                            errors.append(
                                f"Test '{test.id}' navigation to '{nav_url}' violates origin policy. "
                                f"Allowed origins: {allowed}. Set allow_cross_origin=True to permit."
                            )

    return errors
