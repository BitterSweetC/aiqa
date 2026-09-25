# AIQA Enterprise Operating Model

This document defines the enterprise operating controls, security boundaries, data lifecycle rules, compatibility policy, and incident response procedures for operating AIQA in production and staging CI/CD environments.

---

## 1. Ownership and Role-Based Access Control (RBAC)

1. **Suite Ownership**:
   - Every enterprise `TestSuite` declares an `owner` field (e.g., `"owner": "checkout-platform-team"` or PagerDuty/Slack escalation handle) identifying the engineering team responsible for maintaining the suite, rotating its test accounts, and triaging failures or orphaned test entities.
2. **Role Accounts (`suite.roles` and `test.role`)**:
   - Suites map logical role names (e.g., `viewer`, `editor`, `admin`) to isolated Playwright `storage_state` artifacts or dictionaries via `suite.roles`.
   - Individual test cases request the least-privileged role needed via `test.role` (or CLI `--role <role>` on `aiqa plan` and `aiqa auto`).
   - Role accounts must be dedicated, non-human service principals scoped strictly to the target staging/test environment. Production human credentials must never be used.

---

## 2. Authentication and Secret Lifecycle

1. **Storage-State Hygiene and Expiry Validation**:
   - Before launching a browser context, `aiqa.auth.validate_storage_state` inspects every cookie `expires` timestamp and every `localStorage` JWT `exp` claim.
   - Expired cookies or JWTs fail fast before browser startup with a deterministic authentication validation error so CI runs never waste time on stale sessions.
   - `storage_state` files must never be committed to source control; `.gitignore` blocks `*storage_state*.json` and `.auth/`.
2. **CI Secret Injection (`${ENV_VAR}`)**:
   - Test definitions must never hardcode passwords, API keys, or bearer tokens.
   - Action values and fixture headers/bodies reference environment variables using `${ENV_VAR}` syntax (e.g., `"value": "${STAGING_EDITOR_PASSWORD}"`).
   - `aiqa.auth.resolve_test_case_secrets` resolves placeholders from the CI environment at runtime, automatically registers resolved values with `aiqa.security.redaction.register_secret`, and fails fast if a required environment variable is missing or empty.

---

## 3. Test Data and Fixture Lifecycle

1. **Explicit Setup and Teardown (`FixtureSpec`)**:
   - Stateful tests provision isolated test data via `setup_fixtures` and clean up via `teardown_fixtures` (`aiqa.fixtures.FixtureLifecycleManager`).
   - Setup fixtures extract created entity IDs via `extract_id_field` (default `"id"`) and bind them to `{TOKEN}` placeholders (e.g., `{ENTITY_ID}`) across test URLs, actions, expectations, and teardown endpoints.
2. **Guaranteed Teardown & Orphan Reporting**:
   - `TestRunner` executes `teardown_fixtures` in reverse (LIFO) order inside a `finally` block so teardown runs even when browser actions, assertions, or timeouts fail.
   - Every created entity is recorded in `TestResult.created_entities` (`CreatedEntityRecord`), and any failed teardown is surfaced in `TestResult.cleanup_errors` and `RunSummary.cleanup_failures` so CI pipelines can alert on orphaned test state independently of test pass/fail status.

---

## 4. Artifact Retention, Redaction, and Audit Logging

1. **Automatic Secret Redaction**:
   - All registered secrets, sensitive selectors (`input[type='password']`, token/secret/cvv/ssn inputs), Bearer tokens, API keys (`sk-...`), and URL query credentials are redacted (`***REDACTED***`) before being written to `TestResult`, JSON reports, JUnit XML reports, HTML dashboards, failure diagnoses, or audit logs.
2. **Audit Logging (`aiqa.security.AuditLogger`)**:
   - Enterprise runs record structured, redacted JSONL events (`suite_run`, `policy_violation`, `fixture_entity`) capturing timestamp, actor/owner, role, test ID, status, and cleanup state.
3. **Artifact Retention (`aiqa.security.RetentionManager` / `aiqa retention`)**:
   - Reports, screenshots, and downloaded files must be pruned according to environment retention bounds (default: 30 days in CI artifact storage).
   - Operators and CI cron jobs enforce retention via:
     ```bash
     aiqa retention --dir ./reports --max-age-days 30 --max-files 200
     ```

---

## 5. Supported vs. Unsupported Browser Surfaces

| Browser Surface | Support Status | Contract / Mechanism |
| :--- | :--- | :--- |
| Standard DOM & SPA routing | **Supported** | `ClickAction`, `FillAction`, `PressAction`, `NavigateAction`, `DomVerifier`, `UrlVerifier` |
| `<iframe>` (same-origin & permitted frames) | **Supported** | `frame_selector` on `ClickAction`, `FillAction`, `PressAction`, and `Expectation(type="dom")` via Playwright `page.frame_locator` |
| Open Shadow DOM (`mode: 'open'`) | **Supported** | Automatic piercing via Playwright `page.locator` in `ActionDriver` and `DomVerifier` |
| Closed Shadow DOM (`mode: 'closed'`) | **Unsupported** | Closed shadow roots intentionally block external DOM inspection by W3C specification; test via application-exposed hooks or accessible outer controls |
| Popup / OAuth windows (`window.open`, `target="_blank"`) | **Supported** | `PopupAction(action="popup", trigger_selector=..., popup_click_selector=...)` with origin policy validation on the popup URL |
| File uploads (`<input type="file">`) | **Supported** | `UploadAction(action="upload", selector=..., file_paths=[...])` |
| File downloads (`Content-Disposition: attachment`) | **Supported** | `BrowserSession.downloads` listener + `Expectation(type="download", value="filename.ext")` (`DownloadVerifier`) |
| Mobile & custom viewports | **Supported** | `TestCase(is_mobile=True, viewport={"width": 390, "height": 844}, user_agent=...)` |
| Deterministic Accessibility (WCAG / ARIA) | **Supported** | `Expectation(type="a11y", selector=..., value="wcag2a")` (`AccessibilityVerifier`) checking image `alt`, control accessible names, form labels, and `aria-*` attributes |
| Business Rules & Explicit Oracles | **Supported** | `Expectation(type="business_rule", oracle=..., inconclusive_if_missing_oracle=True)` verifying explicit criteria or marking missing-oracle rules as `inconclusive=True` (`failure_category="inconclusive_business_rule"`) |
| Step / Duration / Cost Execution Budgets | **Supported** | `TestCase(max_steps=..., max_cost_usd=..., timeout=...)` and `ActionDriver` budget enforcement (`code="budget_exceeded"`) |
| Native OS dialogs (Print / OS File Picker / WebAuthn hardware keys) | **Unsupported** | Bypass via `<input type="file">` (`UploadAction`) or virtual authenticator / API fixtures |

---

## 6. Compatibility and Deprecation Policy

1. **Schema Stability**:
   - `TestSuite`, `TestCase`, `BrowserAction`, `Expectation`, and `TestRunReport` follow additive backward compatibility within major versions.
   - Newly introduced fields (`actions`, `planning_notes`, `allowed_origins`, `attempts`, `flaky`, `failure_category`, `role`, `viewport`, `is_mobile`, `setup_fixtures`, `teardown_fixtures`, `created_entities`, `cleanup_errors`, `depends_on`, `max_steps`, `max_cost_usd`, `oracle`, `inconclusive_if_missing_oracle`, `inconclusive`, `inconclusive_reasons`, `verified_features`, `verified_coverage_rate`, `blocked_routes`) always define safe defaults so existing JSON suites and reports remain valid.
2. **Deprecation Process**:
   - Any deprecated schema field or CLI flag must emit a warning for at least one minor release cycle before removal in a major release.

---

## 7. Dependency, Security Review, and Incident Response

1. **Dependency & CI Gate**:
   - Every pull request must pass `ruff check .` (0 lint errors) and the full `pytest` suite (including Phase 0–3 contract, security, scale, and enterprise integration gates).
2. **Incident Response for Exposed Secrets or Live Data**:
   - If a secret or live PII is ever discovered in a test suite or artifact:
     1. **Revoke & Rotate Immediately**: Revoke the affected credential/token or invalidate the session in the target identity provider.
     2. **Purge Artifacts**: Run `aiqa retention --dir ./reports --max-age-days 0` (or delete the CI build artifacts) to purge cached HTML/JSON/screenshot files.
     3. **Patch Redaction Rules**: Add the new token pattern or selector rule to `aiqa/security/redaction.py` with a regression test in `tests/test_phase1_ci_safety.py`.
     4. **Audit Trail Review**: Inspect the JSONL audit log (`AuditLogger`) to identify all runs and entities touched by the affected role or suite.
