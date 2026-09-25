"""Explicit test data setup and teardown fixture lifecycle manager."""

from __future__ import annotations

import asyncio
import json
import urllib.error
import urllib.request
from collections.abc import Sequence
from typing import Any
from urllib.parse import urljoin

from aiqa.models.test_case import (
    CreatedEntityRecord,
    FillAction,
    FixtureSpec,
    NavigateAction,
    TestCase,
)
from aiqa.security.policy import is_origin_allowed, is_safe_url_scheme
from aiqa.security.redaction import redact_text


def _sync_http_request(
    method: str,
    url: str,
    headers: dict[str, str],
    payload: dict[str, Any] | None,
    timeout: float = 10.0,
) -> tuple[int, str]:
    body_bytes: bytes | None = None
    req_headers = dict(headers)
    if payload is not None:
        body_bytes = json.dumps(payload).encode("utf-8")
        req_headers.setdefault("Content-Type", "application/json")

    req = urllib.request.Request(url, data=body_bytes, headers=req_headers, method=method.upper())
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            status_code = int(resp.status)
            resp_body = resp.read().decode("utf-8", errors="replace")
            return status_code, resp_body
    except urllib.error.HTTPError as http_err:
        err_body = http_err.read().decode("utf-8", errors="replace") if http_err.fp else ""
        return int(http_err.code), err_body


def _substitute_tokens(text: str, mapping: dict[str, str]) -> str:
    result = text
    for token, val in mapping.items():
        result = result.replace(token, val)
    return result


class FixtureLifecycleManager:
    """Executes setup/teardown API fixtures, tracks created entities, and records cleanup errors."""

    def __init__(
        self,
        base_url: str,
        allowed_origins: Sequence[str] | None = None,
        allow_cross_origin: bool = False,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.allowed_origins = list(allowed_origins or ([self.base_url] if self.base_url else []))
        self.allow_cross_origin = allow_cross_origin
        self.created_entities: list[CreatedEntityRecord] = []
        self._teardown_specs_for_entities: list[tuple[CreatedEntityRecord, FixtureSpec]] = []
        self.cleanup_errors: list[str] = []
        self.token_map: dict[str, str] = {}

    def _resolve_url(self, raw_url: str) -> str:
        resolved = (
            urljoin(self.base_url + "/", raw_url.lstrip("/"))
            if raw_url.startswith("/")
            else raw_url
        )
        if not is_safe_url_scheme(resolved):
            raise ValueError(f"Fixture URL '{resolved}' uses an unsafe URL scheme.")
        if self.allowed_origins and not is_origin_allowed(
            resolved,
            self.allowed_origins,
            allow_cross_origin=self.allow_cross_origin,
        ):
            raise ValueError(
                f"Fixture URL '{resolved}' violates origin constraint policy "
                f"(allowed: {self.allowed_origins})."
            )
        return resolved

    async def run_setup(self, fixtures: Sequence[FixtureSpec]) -> None:
        """Execute setup fixtures in order and record created entity IDs."""
        for spec in fixtures:
            url = self._resolve_url(_substitute_tokens(spec.url, self.token_map))
            status, body = await asyncio.to_thread(
                _sync_http_request,
                spec.method,
                url,
                spec.headers,
                spec.payload,
            )
            if status < 200 or status >= 300:
                raise RuntimeError(
                    f"Setup fixture '{spec.name}' failed with HTTP {status}: {redact_text(body[:200])}"
                )

            entity_id = ""
            if body.strip():
                try:
                    parsed = json.loads(body)
                    if isinstance(parsed, dict):
                        raw_id = (
                            parsed.get(spec.entity_id_field)
                            or parsed.get("id")
                            or parsed.get("entity_id")
                        )
                        if raw_id is not None:
                            entity_id = str(raw_id)
                except Exception:  # noqa: BLE001
                    entity_id = ""

            if not entity_id:
                entity_id = f"{spec.name}-created"

            safe_key = "".join(c if c.isalnum() else "_" for c in spec.name.upper())
            self.token_map["{ENTITY_ID}"] = entity_id
            self.token_map[f"{{FIXTURE_{safe_key}_ID}}"] = entity_id

            teardown_url = (
                self._resolve_url(
                    _substitute_tokens(
                        spec.teardown_url_template.replace("{entity_id}", entity_id),
                        self.token_map,
                    )
                )
                if spec.teardown_url_template
                else url
            )
            record = CreatedEntityRecord(
                fixture_name=spec.name,
                entity_id=entity_id,
                resource_url=teardown_url,
                cleaned_up=False,
            )
            self.created_entities.append(record)
            if spec.teardown_url_template:
                self._teardown_specs_for_entities.append((record, spec))

    def bind_test_tokens(self, test: TestCase) -> TestCase:
        """Substitute created entity tokens (`{ENTITY_ID}`, `{FIXTURE_<NAME>_ID}`) into a TestCase."""
        if not self.token_map:
            return test

        new_start_url = _substitute_tokens(test.start_url, self.token_map)
        new_actions = None
        if test.actions is not None:
            updated_actions = []
            for act in test.actions:
                if isinstance(act, NavigateAction):
                    updated_actions.append(
                        act.model_copy(
                            update={"url": _substitute_tokens(act.url, self.token_map)}
                        )
                    )
                elif isinstance(act, FillAction):
                    updated_actions.append(
                        act.model_copy(
                            update={
                                "selector": _substitute_tokens(act.selector, self.token_map),
                                "value": _substitute_tokens(act.value, self.token_map),
                            }
                        )
                    )
                elif hasattr(act, "selector") and getattr(act, "selector", None):
                    updated_actions.append(
                        act.model_copy(
                            update={
                                "selector": _substitute_tokens(
                                    str(getattr(act, "selector", "")), self.token_map
                                )
                            }
                        )
                    )
                else:
                    updated_actions.append(act)
            new_actions = updated_actions

        new_expected = [
            exp.model_copy(
                update={
                    "selector": (
                        _substitute_tokens(exp.selector, self.token_map)
                        if exp.selector
                        else None
                    ),
                    "value": (
                        _substitute_tokens(exp.value, self.token_map) if exp.value else None
                    ),
                }
            )
            for exp in test.expected
        ]
        return test.model_copy(
            update={
                "start_url": new_start_url,
                "actions": new_actions,
                "expected": new_expected,
            }
        )

    async def run_teardown(self, explicit_teardowns: Sequence[FixtureSpec] = ()) -> list[str]:
        """Run teardown for all created entities and explicit teardown fixtures.

        Never raises; records any teardown failures in `self.cleanup_errors`.
        """
        # 1. Teardown entities created during setup in reverse (LIFO) order
        for record, spec in reversed(self._teardown_specs_for_entities):
            try:
                status, body = await asyncio.to_thread(
                    _sync_http_request,
                    spec.teardown_method,
                    record.resource_url,
                    spec.headers,
                    None,
                )
                if 200 <= status < 300:
                    record.cleaned_up = True
                else:
                    self.cleanup_errors.append(
                        f"Teardown for '{record.fixture_name}' (entity_id={record.entity_id}) "
                        f"failed with HTTP {status}: {redact_text(body[:160])}"
                    )
            except Exception as exc:  # noqa: BLE001
                self.cleanup_errors.append(
                    f"Teardown exception for '{record.fixture_name}' "
                    f"(entity_id={record.entity_id}): {redact_text(str(exc))}"
                )

        # 2. Run any explicit teardown_fixtures
        for spec in explicit_teardowns:
            try:
                raw_url = _substitute_tokens(
                    spec.url.replace("{entity_id}", self.token_map.get("{ENTITY_ID}", "")),
                    self.token_map,
                )
                url = self._resolve_url(raw_url)
                status, body = await asyncio.to_thread(
                    _sync_http_request,
                    spec.method,
                    url,
                    spec.headers,
                    spec.payload,
                )
                if 200 <= status < 300:
                    for rec in self.created_entities:
                        if rec.entity_id in url:
                            rec.cleaned_up = True
                else:
                    self.cleanup_errors.append(
                        f"Explicit teardown '{spec.name}' failed with HTTP {status}: "
                        f"{redact_text(body[:160])}"
                    )
            except Exception as exc:  # noqa: BLE001
                self.cleanup_errors.append(
                    f"Explicit teardown '{spec.name}' error: {redact_text(str(exc))}"
                )

        return list(self.cleanup_errors)
