"""Phase 3 verification tests: Enterprise Auth, Fixtures, Browser Surfaces, A11y, and Operating Controls."""

from __future__ import annotations

import base64
import json
import os
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, ClassVar

import pytest

from aiqa.auth.workflow import validate_storage_state
from aiqa.models.test_case import (
    ClickAction,
    Expectation,
    FillAction,
    FixtureSpec,
    PopupAction,
    TestCase,
    TestSuite,
    UploadAction,
)
from aiqa.orchestrator.runner import TestRunner
from aiqa.security.audit import AuditLogger
from aiqa.security.redaction import clear_registered_secrets
from aiqa.security.retention import RetentionManager


class _EnterpriseAppHandler(BaseHTTPRequestHandler):
    """Stateful multi-role enterprise fixture web application for Phase 3 browser tests."""

    entities: ClassVar[dict[str, dict[str, Any]]] = {}
    deleted_ids: ClassVar[list[str]] = []

    def log_message(self, format: str, *args: Any) -> None:
        pass

    def _send_html(self, html: str, status: int = 200) -> None:
        body = html.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_json(self, payload: dict[str, Any], status: int = 200) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _get_role_cookie(self) -> str:
        cookie_hdr = self.headers.get("Cookie", "")
        for part in cookie_hdr.split(";"):
            part = part.strip()
            if part.startswith("aiqa_role="):
                return part.split("=", 1)[1].strip()
        return "anonymous"

    def do_POST(self) -> None:
        length = int(self.headers.get("Content-Length", "0"))
        raw = self.rfile.read(length).decode("utf-8") if length > 0 else "{}"
        data = json.loads(raw) if raw else {}

        if self.path == "/api/entities":
            entity_id = f"ENT-{len(self.entities) + 101}"
            record = {"id": entity_id, "name": data.get("name", "Unnamed")}
            self.entities[entity_id] = record
            self._send_json(record, status=201)
            return

        self._send_json({"error": "Not found"}, status=404)

    def do_DELETE(self) -> None:
        if self.path.startswith("/api/entities/fail-cleanup/"):
            self._send_json({"error": "Simulated cleanup failure"}, status=500)
            return

        if self.path.startswith("/api/entities/"):
            entity_id = self.path.rsplit("/", 1)[-1]
            self.entities.pop(entity_id, None)
            self.deleted_ids.append(entity_id)
            self._send_json({"deleted": entity_id}, status=200)
            return

        self._send_json({"error": "Not found"}, status=404)

    def do_GET(self) -> None:
        if self.path == "/dashboard":
            role = self._get_role_cookie()
            panel = (
                "Role: admin - Secret Control Panel"
                if role == "admin"
                else f"Role: {role} - Read Only"
            )
            self._send_html(
                f"""<!DOCTYPE html>
                <html lang="en"><head><title>Enterprise Dashboard</title></head>
                <body>
                  <h1 id="role-banner">{panel}</h1>
                  <label for="token-input">API Token</label>
                  <input id="token-input" type="password" />
                  <button id="verify-token-btn" onclick="
                    const val = document.getElementById('token-input').value;
                    document.getElementById('auth-result').textContent = val ? 'Token Accepted' : 'Missing';
                  ">Verify Token</button>
                  <div id="auth-result">Pending</div>
                </body></html>"""
            )
            return

        if self.path.startswith("/entity/"):
            entity_id = self.path.rsplit("/", 1)[-1]
            entity = self.entities.get(entity_id)
            title = entity["name"] if entity else "Not Found"
            self._send_html(
                f"""<!DOCTYPE html>
                <html lang="en"><head><title>Entity View</title></head>
                <body>
                  <h1 id="entity-header">{entity_id}: {title}</h1>
                  <button id="Ack-btn" onclick="document.getElementById('ack-state').textContent='Acknowledged {entity_id}';">Acknowledge</button>
                  <div id="ack-state">Unacknowledged</div>
                </body></html>"""
            )
            return

        if self.path == "/iframe-inner":
            self._send_html(
                """<!DOCTYPE html>
                <html lang="en"><head><title>Inner Frame</title></head>
                <body>
                  <label for="frame-input">Frame Input</label>
                  <input id="frame-input" type="text" />
                  <button id="frame-submit" onclick="
                    document.getElementById('frame-output').textContent =
                      'Submitted: ' + document.getElementById('frame-input').value;
                  ">Submit Frame</button>
                  <div id="frame-output">Idle</div>
                </body></html>"""
            )
            return

        if self.path == "/oauth-popup":
            self._send_html(
                """<!DOCTYPE html>
                <html lang="en"><head><title>OAuth Consent</title></head>
                <body>
                  <button id="approve-oauth" onclick="
                    if (window.opener) {
                      window.opener.document.getElementById('oauth-status').textContent = 'OAuth Connected';
                    }
                    window.close();
                  ">Approve OAuth</button>
                </body></html>"""
            )
            return

        if self.path == "/download/audit_export.csv":
            csv_bytes = b"id,event,status\n1,login,ok\n2,export,ok\n"
            self.send_response(200)
            self.send_header("Content-Type", "text/csv")
            self.send_header(
                "Content-Disposition", 'attachment; filename="audit_export.csv"'
            )
            self.send_header("Content-Length", str(len(csv_bytes)))
            self.end_headers()
            self.wfile.write(csv_bytes)
            return

        if self.path == "/surfaces":
            self._send_html(
                """<!DOCTYPE html>
                <html lang="en"><head>
                  <meta name="viewport" content="width=device-width, initial-scale=1" />
                  <title>Enterprise Surfaces</title>
                </head>
                <body>
                  <h1>Complex Browser Surfaces</h1>
                  <iframe id="embedded-frame" src="/iframe-inner"></iframe>

                  <div id="shadow-host"></div>
                  <script>
                    const host = document.getElementById('shadow-host');
                    const root = host.attachShadow({mode: 'open'});
                    root.innerHTML = `
                      <button id="shadow-btn">Activate Shadow</button>
                      <span id="shadow-status">Shadow Idle</span>
                    `;
                    root.getElementById('shadow-btn').addEventListener('click', () => {
                      root.getElementById('shadow-status').textContent = 'Shadow Activated';
                    });
                  </script>

                  <button id="oauth-login-btn" onclick="window.open('/oauth-popup', 'oauth', 'width=400,height=300');">
                    Connect OAuth
                  </button>
                  <div id="oauth-status">OAuth Disconnected</div>

                  <label for="file-upload">Upload File</label>
                  <input id="file-upload" type="file" onchange="
                    const f = this.files[0];
                    document.getElementById('upload-result').textContent = f ? ('Uploaded: ' + f.name) : 'None';
                  " />
                  <div id="upload-result">None</div>

                  <a id="download-report" href="/download/audit_export.csv" download="audit_export.csv">
                    Download Audit CSV
                  </a>

                  <div id="viewport-badge">Desktop Layout</div>
                  <script>
                    if (window.innerWidth <= 480) {
                      document.getElementById('viewport-badge').textContent = 'Mobile Layout';
                    }
                  </script>
                </body></html>"""
            )
            return

        if self.path == "/accessible":
            self._send_html(
                """<!DOCTYPE html>
                <html lang="en"><head><title>Accessible Portal</title></head>
                <body>
                  <h1>Accessible Enterprise Portal</h1>
                  <img src="data:image/gif;base64,R0lGODlhAQABAIAAAAAAAP///yH5BAEAAAAALAAAAAABAAEAAAIBRAA7" alt="Company Logo" />
                  <label for="full-name">Full Name</label>
                  <input id="full-name" type="text" />
                  <button id="save-btn" onclick="document.getElementById('save-msg').textContent='Saved Accessible Form';">
                    Save Profile
                  </button>
                  <div id="save-msg" role="status" aria-live="polite">Ready</div>
                </body></html>"""
            )
            return

        if self.path == "/inaccessible":
            self._send_html(
                """<!DOCTYPE html>
                <html lang="en"><head><title>Inaccessible Portal</title></head>
                <body>
                  <h1>Inaccessible Page</h1>
                  <img src="data:image/gif;base64,R0lGODlhAQABAIAAAAAAAP///yH5BAEAAAAALAAAAAABAAEAAAIBRAA7" />
                  <input id="unlabeled-field" type="text" />
                  <button id="empty-btn" onclick="document.getElementById('bad-msg').textContent='Clicked';"></button>
                  <div id="bad-msg">Idle</div>
                </body></html>"""
            )
            return

        self._send_html("<h1>404</h1>", status=404)


@pytest.fixture()
def enterprise_server():
    _EnterpriseAppHandler.entities.clear()
    _EnterpriseAppHandler.deleted_ids.clear()
    server = ThreadingHTTPServer(("127.0.0.1", 0), _EnterpriseAppHandler)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{port}", _EnterpriseAppHandler
    server.shutdown()
    server.server_close()


def _make_jwt(exp_epoch: float) -> str:
    header = base64.urlsafe_b64encode(b'{"alg":"HS256","typ":"JWT"}').decode().rstrip("=")
    payload = (
        base64.urlsafe_b64encode(json.dumps({"sub": "u1", "exp": exp_epoch}).encode())
        .decode()
        .rstrip("=")
    )
    return f"{header}.{payload}.sig"


@pytest.mark.asyncio
async def test_phase3_rbac_roles_and_ci_secret_injection(
    enterprise_server: tuple[str, Any],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify multi-role storage_state isolation, expiry checks, and ${ENV_VAR} secret redaction."""
    base_url, _ = enterprise_server
    clear_registered_secrets()
    monkeypatch.setenv("AIQA_ENTERPRISE_TOKEN", "ent-secret-token-998877")

    future_exp = time.time() + 3600
    roles_cfg = {
        "admin": {
            "cookies": [
                {
                    "name": "aiqa_role",
                    "value": "admin",
                    "domain": "127.0.0.1",
                    "path": "/",
                    "expires": future_exp,
                }
            ],
            "origins": [],
        },
        "viewer": {
            "cookies": [
                {
                    "name": "aiqa_role",
                    "value": "viewer",
                    "domain": "127.0.0.1",
                    "path": "/",
                    "expires": future_exp,
                }
            ],
            "origins": [],
        },
    }

    suite = TestSuite(
        name="RBAC & Secret Suite",
        base_url=base_url,
        owner="identity-platform-team",
        roles=roles_cfg,
        tests=[
            TestCase(
                id="RBAC-ADMIN",
                name="Admin Role Access & Secret Injection",
                start_url=f"{base_url}/dashboard",
                goal="Verify admin role banner and inject CI secret token",
                role="admin",
                actions=[
                    FillAction(
                        action="fill",
                        selector="#token-input",
                        value="${AIQA_ENTERPRISE_TOKEN}",
                    ),
                    ClickAction(action="click", selector="#verify-token-btn"),
                ],
                expected=[
                    Expectation(
                        type="dom",
                        selector="#role-banner",
                        value="Role: admin - Secret Control Panel",
                        description="Admin control panel is visible",
                    ),
                    Expectation(
                        type="dom",
                        selector="#auth-result",
                        value="Token Accepted",
                        description="Injected token was accepted",
                    ),
                ],
            ),
            TestCase(
                id="RBAC-VIEWER",
                name="Viewer Role Isolation",
                start_url=f"{base_url}/dashboard",
                goal="Verify viewer role sees read-only banner",
                role="viewer",
                actions=[
                    ClickAction(action="click", selector="#verify-token-btn"),
                ],
                expected=[
                    Expectation(
                        type="dom",
                        selector="#role-banner",
                        value="Role: viewer - Read Only",
                        description="Viewer sees read-only banner",
                    ),
                    Expectation(
                        type="dom",
                        selector="#auth-result",
                        value="Missing",
                        description="Viewer click without token shows Missing",
                    ),
                ],
            ),
        ],
    )

    runner = TestRunner(headless=True, screenshots_dir=tmp_path / "screenshots")
    report = await runner.run_suite(suite)

    assert report.summary.total == 2
    assert report.summary.passed == 2
    serialized = report.model_dump_json()
    assert "ent-secret-token-998877" not in serialized
    assert "[REDACTED]" in serialized

    # Verify expired cookie and expired JWT fail validation before browser launch
    expired_cookie_state = {
        "cookies": [{"name": "sid", "value": "123", "expires": time.time() - 120}],
        "origins": [],
    }
    with pytest.raises(ValueError, match="Expired cookie"):
        validate_storage_state(expired_cookie_state)

    expired_jwt_state = {
        "cookies": [],
        "origins": [
            {
                "origin": base_url,
                "localStorage": [{"name": "id_token", "value": _make_jwt(time.time() - 60)}],
            }
        ],
    }
    with pytest.raises(ValueError, match="Expired JWT"):
        validate_storage_state(expired_jwt_state)


@pytest.mark.asyncio
async def test_phase3_fixture_lifecycle_and_guaranteed_teardown(
    enterprise_server: tuple[str, Any],
    tmp_path: Path,
) -> None:
    """Verify API setup fixtures, {ENTITY_ID} token binding, and teardown even on test failure."""
    base_url, handler_cls = enterprise_server

    suite = TestSuite(
        name="Fixture Lifecycle Suite",
        base_url=base_url,
        tests=[
            TestCase(
                id="FIX-PASS",
                name="Create, Verify, and Clean Up Entity",
                start_url=f"{base_url}/entity/{{ENTITY_ID}}",
                goal="Acknowledge dynamically created entity",
                setup_fixtures=[
                    FixtureSpec(
                        name="create_order",
                        method="POST",
                        url=f"{base_url}/api/entities",
                        body={"name": "Enterprise Order Alpha"},
                        bind_id_as="ENTITY_ID",
                    )
                ],
                teardown_fixtures=[
                    FixtureSpec(
                        name="delete_order",
                        method="DELETE",
                        url=f"{base_url}/api/entities/{{ENTITY_ID}}",
                    )
                ],
                actions=[
                    ClickAction(action="click", selector="#Ack-btn"),
                ],
                expected=[
                    Expectation(
                        type="dom",
                        selector="#ack-state",
                        value="Acknowledged {ENTITY_ID}",
                        description="Entity acknowledgment reflects bound entity ID",
                    )
                ],
            ),
            TestCase(
                id="FIX-FAIL-CLEANUP",
                name="Failing Test Still Runs Teardown and Reports Cleanup Error",
                start_url=f"{base_url}/entity/{{ENTITY_ID}}",
                goal="Fail assertion and verify teardown runs and records cleanup error",
                setup_fixtures=[
                    FixtureSpec(
                        name="create_temp_entity",
                        method="POST",
                        url=f"{base_url}/api/entities",
                        body={"name": "Will Fail"},
                        bind_id_as="ENTITY_ID",
                    ),
                    FixtureSpec(
                        name="create_uncleanable",
                        method="POST",
                        url=f"{base_url}/api/entities",
                        body={"name": "Uncleanable"},
                        bind_id_as="BAD_ID",
                    ),
                ],
                teardown_fixtures=[
                    FixtureSpec(
                        name="delete_temp_entity",
                        method="DELETE",
                        url=f"{base_url}/api/entities/{{ENTITY_ID}}",
                    ),
                    FixtureSpec(
                        name="delete_uncleanable",
                        method="DELETE",
                        url=f"{base_url}/api/entities/fail-cleanup/{{BAD_ID}}",
                    ),
                ],
                actions=[
                    ClickAction(action="click", selector="#Ack-btn"),
                ],
                expected=[
                    Expectation(
                        type="dom",
                        selector="#ack-state",
                        value="Nonexistent State Value",
                        description="Intentional failure to test guaranteed teardown",
                    )
                ],
            ),
        ],
    )

    runner = TestRunner(headless=True, screenshots_dir=tmp_path / "screenshots")
    report = await runner.run_suite(suite)

    res_pass = report.results[0]
    assert res_pass.status == "pass"
    assert len(res_pass.created_entities) == 1
    assert res_pass.created_entities[0].cleaned_up is True
    assert res_pass.cleanup_errors == []
    assert res_pass.created_entities[0].entity_id in handler_cls.deleted_ids

    res_fail = report.results[1]
    assert res_fail.status == "fail"
    # Even though the test failed its DOM assertion, delete_temp_entity still ran!
    first_entity_id = res_fail.created_entities[0].entity_id
    assert first_entity_id in handler_cls.deleted_ids
    # And the failing teardown endpoint is explicitly reported in cleanup_errors & summary.cleanup_failures
    assert len(res_fail.cleanup_errors) == 1
    assert "delete_uncleanable" in res_fail.cleanup_errors[0]
    assert report.summary.cleanup_failures == 1


@pytest.mark.asyncio
async def test_phase3_complex_browser_surfaces_and_a11y(
    enterprise_server: tuple[str, Any],
    tmp_path: Path,
) -> None:
    """Verify iframes, open Shadow DOM, popups, file upload, file download, mobile viewport, and a11y."""
    base_url, _ = enterprise_server
    upload_file = tmp_path / "sample_evidence.txt"
    upload_file.write_text("enterprise upload payload", encoding="utf-8")

    suite = TestSuite(
        name="Enterprise Surfaces & Accessibility Suite",
        base_url=base_url,
        tests=[
            TestCase(
                id="SURF-ALL",
                name="Iframe, Shadow DOM, OAuth Popup, Upload, Download, and Mobile Viewport",
                start_url=f"{base_url}/surfaces",
                goal="Exercise all complex enterprise browser surfaces on a mobile viewport",
                is_mobile=True,
                actions=[
                    FillAction(
                        action="fill",
                        frame_selector="#embedded-frame",
                        selector="#frame-input",
                        value="Enterprise Iframe",
                    ),
                    ClickAction(
                        action="click",
                        frame_selector="#embedded-frame",
                        selector="#frame-submit",
                    ),
                    ClickAction(action="click", selector="#shadow-btn"),
                    PopupAction(
                        action="popup",
                        trigger_selector="#oauth-login-btn",
                        popup_click_selector="#approve-oauth",
                    ),
                    UploadAction(
                        action="upload",
                        selector="#file-upload",
                        file_paths=[str(upload_file)],
                    ),
                    ClickAction(action="click", selector="#download-report"),
                ],
                expected=[
                    Expectation(
                        type="dom",
                        frame_selector="#embedded-frame",
                        selector="#frame-output",
                        value="Submitted: Enterprise Iframe",
                        description="Iframe form state updated",
                    ),
                    Expectation(
                        type="dom",
                        selector="#shadow-status",
                        value="Shadow Activated",
                        description="Open Shadow DOM state updated",
                    ),
                    Expectation(
                        type="dom",
                        selector="#oauth-status",
                        value="OAuth Connected",
                        description="OAuth popup updated opener state",
                    ),
                    Expectation(
                        type="dom",
                        selector="#upload-result",
                        value="Uploaded: sample_evidence.txt",
                        description="File upload succeeded",
                    ),
                    Expectation(
                        type="download",
                        value="audit_export.csv",
                        description="CSV report downloaded with non-zero size",
                    ),
                    Expectation(
                        type="dom",
                        selector="#viewport-badge",
                        value="Mobile Layout",
                        description="Mobile viewport rendered mobile layout",
                    ),
                ],
            ),
            TestCase(
                id="A11Y-PASS",
                name="Accessible Page Passes WCAG/ARIA Audit",
                start_url=f"{base_url}/accessible",
                goal="Save accessible profile and verify WCAG compliance",
                actions=[
                    FillAction(action="fill", selector="#full-name", value="Ada Lovelace"),
                    ClickAction(action="click", selector="#save-btn"),
                ],
                expected=[
                    Expectation(
                        type="dom",
                        selector="#save-msg",
                        value="Saved Accessible Form",
                        description="Profile saved",
                    ),
                    Expectation(
                        type="a11y",
                        value="wcag2a",
                        description="Page has zero deterministic WCAG/ARIA violations",
                    ),
                ],
            ),
            TestCase(
                id="A11Y-FAIL",
                name="Inaccessible Page Fails WCAG/ARIA Audit",
                start_url=f"{base_url}/inaccessible",
                goal="Detect missing alt text, unlabeled input, and empty button",
                actions=[
                    ClickAction(action="click", selector="#empty-btn"),
                ],
                expected=[
                    Expectation(
                        type="a11y",
                        value="wcag2a",
                        description="Must fail when accessibility violations exist",
                    ),
                ],
            ),
        ],
    )

    runner = TestRunner(headless=True, screenshots_dir=tmp_path / "screenshots")
    report = await runner.run_suite(suite)

    assert report.results[0].status == "pass", report.results[0].error_message
    assert report.results[1].status == "pass", report.results[1].error_message
    assert report.results[2].status == "fail"
    assert "missing [alt] attribute" in (report.results[2].error_message or "")
    assert "empty accessible name" in (report.results[2].error_message or "")
    assert "missing accessible label" in (report.results[2].error_message or "")

    # Verify AuditLogger and RetentionManager operating controls
    audit_file = tmp_path / "audit" / "audit_log.jsonl"
    auditor = AuditLogger(audit_file)
    records = auditor.log_suite_run(report, owner="qa-governance-team")
    assert len(records) >= 1
    loaded_events = auditor.read_events()
    assert loaded_events[0]["event_type"] == "suite_run"
    assert loaded_events[0]["actor"] == "qa-governance-team"

    # Create old and new artifact files and prune via RetentionManager
    artifacts_dir = tmp_path / "ret_reports"
    artifacts_dir.mkdir(parents=True, exist_ok=True)
    old_file = artifacts_dir / "old_report.json"
    new_file = artifacts_dir / "new_report.json"
    old_file.write_text("{}", encoding="utf-8")
    new_file.write_text("{}", encoding="utf-8")
    now = time.time()
    os.utime(old_file, (now - 40 * 86400, now - 40 * 86400))
    os.utime(new_file, (now, now))

    retention_mgr = RetentionManager(max_age_days=30.0)
    prune_res = retention_mgr.prune(artifacts_dir, now_epoch=now)
    assert prune_res["pruned_count"] == 1
    assert prune_res["retained_count"] == 1
    assert not old_file.exists()
    assert new_file.exists()
