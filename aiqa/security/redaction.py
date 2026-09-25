"""Secret and sensitive content redaction for AIQA traces, logs, and reports."""

from __future__ import annotations

import re
from collections.abc import Iterable
from typing import Any

_REGISTERED_SECRETS: set[str] = set()

_SENSITIVE_SELECTOR_PATTERN = re.compile(
    r"(?:password|passwd|pwd|secret|token|api[-_]?key|cvv|cvc|ssn|credit[-_]?card|card[-_]?number|auth[-_]?code)",
    re.IGNORECASE,
)

_SENSITIVE_KEY_PATTERN = re.compile(
    r"^(?:password|passwd|pwd|secret|token|api[-_]?key|access[-_]?token|refresh[-_]?token|"
    r"client[-_]?secret|authorization|cookie|set-cookie|cvv|cvc|ssn)$",
    re.IGNORECASE,
)

_BEARER_PATTERN = re.compile(r"\b(Bearer\s+)[A-Za-z0-9\-._~+/]+=*", re.IGNORECASE)
_API_KEY_PATTERN = re.compile(
    r"\b(?:sk-[A-Za-z0-9_-]{8,}|gh[pousr]_[A-Za-z0-9]{10,}|glpat-[A-Za-z0-9_-]{8,}|"
    r"xox[bp]-[A-Za-z0-9_-]{8,}|AKIA[0-9A-Z]{16})\b"
)
_URL_QUERY_SECRET_PATTERN = re.compile(
    r"([?&](?:password|passwd|secret|token|api_key|apikey|key|access_token|auth_token|client_secret)=)([^&\s'\"#]+)",
    re.IGNORECASE,
)
_KV_SECRET_PATTERN = re.compile(
    r"([\"']?(?:password|passwd|secret|api_key|apikey|access_token|client_secret)[\"']?\s*[:=]\s*[\"'])([^\"']+)([\"'])",
    re.IGNORECASE,
)


def register_secret(secret: str) -> None:
    """Register a runtime secret string to be redacted from all outputs."""
    if secret and len(secret.strip()) >= 4:
        _REGISTERED_SECRETS.add(secret.strip())


def clear_registered_secrets() -> None:
    """Clear dynamically registered secrets (primarily for test isolation)."""
    _REGISTERED_SECRETS.clear()


def get_registered_secrets() -> set[str]:
    """Return a copy of currently registered dynamic secrets."""
    return set(_REGISTERED_SECRETS)


def is_sensitive_selector(selector: str | None, description: str = "") -> bool:
    """Return True if a CSS selector or action description targets sensitive input."""
    combined = f"{selector or ''} {description or ''}"
    return bool(_SENSITIVE_SELECTOR_PATTERN.search(combined))


def redact_text(text: str | None, extra_secrets: Iterable[str] = ()) -> str:
    """Redact secrets, tokens, API keys, and sensitive query params from text."""
    if not text:
        return "" if text is None else text

    out = text

    # 1. Explicit registered secrets
    all_secrets = sorted(
        _REGISTERED_SECRETS | {s.strip() for s in extra_secrets if s and len(s.strip()) >= 4},
        key=len,
        reverse=True,
    )
    for sec in all_secrets:
        if sec in out:
            out = out.replace(sec, "[REDACTED]")

    # 2. Pattern-based redaction
    out = _BEARER_PATTERN.sub(r"\1[REDACTED]", out)
    out = _API_KEY_PATTERN.sub("[REDACTED_API_KEY]", out)
    out = _URL_QUERY_SECRET_PATTERN.sub(r"\1[REDACTED]", out)
    out = _KV_SECRET_PATTERN.sub(r"\1[REDACTED]\3", out)

    return out


def redact_data(obj: Any, extra_secrets: Iterable[str] = ()) -> Any:
    """Recursively redact sensitive fields and secret patterns across data structures."""
    if obj is None:
        return None
    if isinstance(obj, str):
        return redact_text(obj, extra_secrets=extra_secrets)
    if isinstance(obj, list):
        return [redact_data(item, extra_secrets=extra_secrets) for item in obj]
    if isinstance(obj, tuple):
        return tuple(redact_data(item, extra_secrets=extra_secrets) for item in obj)
    if isinstance(obj, dict):
        redacted_dict: dict[Any, Any] = {}
        for k, v in obj.items():
            key_str = str(k)
            if _SENSITIVE_KEY_PATTERN.match(key_str) and isinstance(v, str) and v or (
                key_str == "value"
                and isinstance(v, str)
                and is_sensitive_selector(
                    str(obj.get("selector", "")),
                    str(obj.get("description", "")),
                )
            ):
                redacted_dict[k] = "[REDACTED]"
            else:
                redacted_dict[k] = redact_data(v, extra_secrets=extra_secrets)
        return redacted_dict
    return obj
