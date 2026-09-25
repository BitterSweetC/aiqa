"""Enterprise authentication workflow, per-role accounts, expiration checks, and CI secret injection."""

from __future__ import annotations

import base64
import json
import os
import re
import stat
import time
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from aiqa.models.test_case import FillAction, TestCase, TestSuite
from aiqa.security.redaction import register_secret

_ENV_VAR_PATTERN = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")
_JWT_PATTERN = re.compile(r"^[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+$")


class RoleAccount(BaseModel):
    """Per-role test account and authentication state metadata."""

    role: str
    owner: str = "qa-platform-team"
    storage_state_path: str | None = None
    storage_state: dict[str, Any] | None = None
    username_env: str | None = None
    password_env: str | None = None
    token_env: str | None = None
    allowed_tags: list[str] = Field(default_factory=list)


class StorageStateValidationResult(BaseModel):
    """Result of validating a Playwright storage_state artifact before browser launch."""

    valid: bool
    expired_cookies: list[str] = Field(default_factory=list)
    expired_jwts: list[str] = Field(default_factory=list)
    permission_warnings: list[str] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)


def _decode_jwt_exp(token: str) -> float | None:
    """Decode the 'exp' claim from a JWT string without external crypto dependencies."""
    if not _JWT_PATTERN.match(token.strip()):
        return None
    parts = token.strip().split(".")
    if len(parts) != 3:
        return None
    payload_b64 = parts[1]
    padded = payload_b64 + "=" * (-len(payload_b64) % 4)
    try:
        raw = base64.urlsafe_b64decode(padded.encode("ascii"))
        data = json.loads(raw.decode("utf-8"))
        exp = data.get("exp")
        if isinstance(exp, (int, float)):
            return float(exp)
    except Exception:  # noqa: BLE001
        return None
    return None


def validate_storage_state(
    state_or_path: Path | str | dict[str, Any],
    *,
    now_epoch: float | None = None,
    require_restricted_permissions: bool = False,
    raise_on_invalid: bool = True,
) -> StorageStateValidationResult:
    """Validate a Playwright storage_state file or dict for expiration and permissions.

    Also registers all cookie values and localStorage tokens with the secret redactor.
    """
    now = now_epoch if now_epoch is not None else time.time()
    expired_cookies: list[str] = []
    expired_jwts: list[str] = []
    permission_warnings: list[str] = []
    errors: list[str] = []

    state_dict: dict[str, Any]
    if isinstance(state_or_path, dict):
        state_dict = state_or_path
    else:
        path = Path(state_or_path)
        if not path.exists():
            res = StorageStateValidationResult(
                valid=False,
                errors=[f"Storage state file does not exist: {path}"],
            )
            if raise_on_invalid:
                raise ValueError("; ".join(res.errors))
            return res
        try:
            mode = path.stat().st_mode
            if mode & (stat.S_IRGRP | stat.S_IROTH | stat.S_IWGRP | stat.S_IWOTH):
                msg = (
                    f"Storage state file '{path}' has group/world permissions "
                    f"({oct(mode & 0o777)}); recommend chmod 0600."
                )
                permission_warnings.append(msg)
                if require_restricted_permissions:
                    errors.append(msg)
        except OSError as exc:
            errors.append(f"Could not stat storage state file '{path}': {exc}")

        try:
            state_dict = json.loads(path.read_text(encoding="utf-8"))
        except Exception as exc:  # noqa: BLE001
            res = StorageStateValidationResult(
                valid=False,
                errors=[f"Invalid JSON in storage state file '{path}': {exc}"],
            )
            if raise_on_invalid:
                raise ValueError("; ".join(res.errors))
            return res

    cookies = state_dict.get("cookies", [])
    if not isinstance(cookies, list):
        errors.append("storage_state 'cookies' field must be a list")
        cookies = []

    for cookie in cookies:
        if not isinstance(cookie, dict):
            continue
        name = str(cookie.get("name", "unnamed"))
        val = str(cookie.get("value", ""))
        if len(val) >= 4:
            register_secret(val)

        expires = cookie.get("expires")
        if isinstance(expires, (int, float)) and expires > 0 and expires <= now:
            expired_cookies.append(name)

        jwt_exp = _decode_jwt_exp(val)
        if jwt_exp is not None and jwt_exp <= now:
            expired_jwts.append(f"cookie:{name}")

    origins = state_dict.get("origins", [])
    if isinstance(origins, list):
        for origin_entry in origins:
            if not isinstance(origin_entry, dict):
                continue
            local_storage = origin_entry.get("localStorage", [])
            if not isinstance(local_storage, list):
                continue
            for item in local_storage:
                if not isinstance(item, dict):
                    continue
                k = str(item.get("name", ""))
                v = str(item.get("value", ""))
                if len(v) >= 6 and any(
                    s in k.lower() for s in ("token", "auth", "secret", "key", "jwt", "session")
                ):
                    register_secret(v)
                jwt_exp = _decode_jwt_exp(v)
                if jwt_exp is not None and jwt_exp <= now:
                    expired_jwts.append(f"localStorage:{k}")

    if expired_cookies:
        errors.append(f"Expired cookie(s): {', '.join(expired_cookies)}")
    if expired_jwts:
        errors.append(f"Expired JWT token(s): {', '.join(expired_jwts)}")

    result = StorageStateValidationResult(
        valid=len(errors) == 0,
        expired_cookies=expired_cookies,
        expired_jwts=expired_jwts,
        permission_warnings=permission_warnings,
        errors=errors,
    )
    if raise_on_invalid and not result.valid:
        raise ValueError("; ".join(result.errors))
    return result


def inject_ci_secrets(
    text: str,
    env: Mapping[str, str] | None = None,
) -> str:
    """Resolve `${ENV_VAR}` placeholders from environment and register resolved secrets."""
    if not text or "${" not in text:
        return text

    source_env = env if env is not None else os.environ

    def _replace(match: re.Match[str]) -> str:
        var_name = match.group(1)
        val = source_env.get(var_name)
        if val is None or val == "":
            raise ValueError(
                f"Required CI environment secret '${{{var_name}}}' is not set or empty."
            )
        register_secret(val)
        return val

    return _ENV_VAR_PATTERN.sub(_replace, text)


def resolve_test_case_secrets(
    test: TestCase,
    env: Mapping[str, str] | None = None,
) -> TestCase:
    """Resolve `${ENV_VAR}` placeholders in a TestCase's FillAction values and fixture headers."""
    new_actions = None
    if test.actions is not None:
        resolved_actions = []
        for act in test.actions:
            if isinstance(act, FillAction) and "${" in act.value:
                resolved_val = inject_ci_secrets(act.value, env=env)
                resolved_actions.append(act.model_copy(update={"value": resolved_val}))
            else:
                resolved_actions.append(act)
        new_actions = resolved_actions

    new_setup = []
    for fix in test.setup_fixtures:
        resolved_headers = {k: inject_ci_secrets(v, env=env) for k, v in fix.headers.items()}
        new_setup.append(fix.model_copy(update={"headers": resolved_headers}))

    new_teardown = []
    for fix in test.teardown_fixtures:
        resolved_headers = {k: inject_ci_secrets(v, env=env) for k, v in fix.headers.items()}
        new_teardown.append(fix.model_copy(update={"headers": resolved_headers}))

    return test.model_copy(
        update={
            "actions": new_actions,
            "setup_fixtures": new_setup,
            "teardown_fixtures": new_teardown,
        }
    )


def resolve_role_storage_state(
    role_or_suite: str | TestSuite | None,
    roles_or_name: Mapping[str, Any] | str | None = None,
    *,
    fallback_storage_state: Path | str | dict[str, Any] | None = None,
    default_storage_state: Path | str | dict[str, Any] | None = None,
    now_epoch: float | None = None,
) -> Path | str | dict[str, Any] | None:
    """Look up and validate the storage_state configured for a role."""
    fallback = (
        fallback_storage_state
        if fallback_storage_state is not None
        else default_storage_state
    )
    if isinstance(role_or_suite, TestSuite):
        roles_cfg: Mapping[str, Any] = getattr(role_or_suite, "roles", {}) or {}
        role_name = str(roles_or_name) if roles_or_name else None
    else:
        role_name = str(role_or_suite) if role_or_suite else None
        roles_cfg = roles_or_name if isinstance(roles_or_name, Mapping) else {}

    if not role_name:
        return fallback

    if role_name not in roles_cfg:
        raise ValueError(
            f"Role '{role_name}' is not defined in suite.roles (available: {list(roles_cfg.keys())})"
        )

    entry = roles_cfg[role_name]
    target_state: Path | str | dict[str, Any] | None
    if isinstance(entry, (str, Path)) or (
        isinstance(entry, dict) and ("cookies" in entry or "origins" in entry)
    ):
        target_state = entry
    elif isinstance(entry, dict):
        role_obj = RoleAccount.model_validate({"role": role_name, **entry})
        target_state = (
            role_obj.storage_state
            if role_obj.storage_state is not None
            else role_obj.storage_state_path
        )
    else:
        target_state = fallback

    if target_state is None:
        return fallback

    validate_storage_state(target_state, now_epoch=now_epoch, raise_on_invalid=True)
    return target_state
