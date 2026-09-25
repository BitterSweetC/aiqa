---
name: aiqa-progress-tracker
description: Step-by-step workflow for developing, verifying, and synchronizing AIQA across Stages 1-5 and Enterprise Phases 0-3. Always activate this skill whenever making any code, CLI, model, test, or benchmark changes to the AIQA repository, or when asked to advance milestones and keep project documentation files synchronized.
---

# AIQA Step-by-Step Progress & Documentation Synchronization Skill

This skill codifies the complete step-by-step engineering workflow for the **AIQA** repository across **Stages 1–5** and **Enterprise Readiness Phases 0–3**, and enforces **mandatory synchronization of project documentation files** whenever any change is made to the project.

---

## Core Rule: Never Leave Documentation Out of Sync

Whenever you modify **any** code, schema, CLI option, verifier, fixture, security control, benchmark, or test in the AIQA repository, you **MUST** follow the 4-step loop below before completing your turn:

1. **Step 1 — Baseline & Incremental Implementation**
2. **Step 2 — Step-by-Step Stage & Phase Verification Gates**
3. **Step 3 — Mandatory Documentation File Synchronization**
4. **Step 4 — Automated Gate Script Execution (`verify_and_check_docs.py`)**

---

## Step 1: Pre-Change & Incremental Development Workflow

1. **Inspect Affected Contracts**:
   - Data models & typed actions: `aiqa/models/test_case.py`, `aiqa/models/coverage.py`
   - Browser & execution pipeline: `aiqa/executor/browser_session.py`, `aiqa/executor/action_driver.py`, `aiqa/executor/jev_runner.py`, `aiqa/orchestrator/runner.py`
   - Security & governance: `aiqa/security/policy.py`, `aiqa/security/redaction.py`, `aiqa/security/audit.py`, `aiqa/security/retention.py`
   - Auth & fixtures: `aiqa/auth/workflow.py`, `aiqa/fixtures/lifecycle.py`
   - Verifiers: `aiqa/verifier/dom.py`, `aiqa/verifier/url.py`, `aiqa/verifier/accessibility.py`, `aiqa/verifier/download.py`, `aiqa/verifier/semantic.py`
   - CLI & reporters: `aiqa/cli.py`, `aiqa/reports/json_report.py`, `aiqa/reports/junit_report.py`, `aiqa/reports/html_report.py`
2. **Write or Update Failing Tests First**:
   - Add unit/integration tests in `tests/` matching the stage or enterprise phase being modified.

---

## Step 2: Step-by-Step Stage & Phase Verification Gates

Run the verification gates in order to ensure zero regressions across the entire architecture:

### Gate A — Stages 1–5 Core Pipeline
- **Scope**: Pydantic v2 schemas, `BrowserSession`, `DomVerifier`/`UrlVerifier`/`SemanticVerifier`, `SiteInspector`, `TestPlanner`, `SiteCrawler`, `CoverageAnalyzer`, `ActionDriver`, `FailureAnalyzer`, `HtmlReporter`.
- **Command**:
  ```bash
  pytest tests/test_models.py tests/test_verifier.py tests/test_runner.py tests/test_planner.py tests/test_crawler_coverage.py tests/test_action_driver.py tests/test_failure_and_html.py tests/test_auto_command.py
  ```

### Gate B — Phase 0: Execution Truth & Test Contracts
- **Scope**:
  - Typed `BrowserAction` contracts (`click`, `fill`, `press`, `navigate`, `wait`, `upload`, `popup`) with strict validation.
  - Fail-closed action propagation (`ActionDriver` -> `JevRunner` -> `TestRunner`).
  - Observable postcondition state diffing (`_capture_postcondition_state`).
  - Explicit `planning_notes` when heuristic planner omits unsupported goals.
- **Command**:
  ```bash
  pytest tests/test_phase0_contracts.py tests/test_browser_smoke.py
  ```

### Gate C — Phase 1: Safe & Reproducible CI Use
- **Scope**:
  - Pre-browser configuration & origin allowlist validation (`aiqa/security/policy.py`).
  - Automatic secret & sensitive-selector redaction (`aiqa/security/redaction.py`).
  - Isolated browser default vs explicit `--cdp` / `--profile` / `storage_state` modes.
  - JUnit XML (`--junit`, `JUnitReporter`) and XSS-escaped HTML reports.
  - Zero Ruff lint findings (`ruff check .`).
- **Command**:
  ```bash
  ruff check .
  pytest tests/test_phase1_ci_safety.py tests/test_browser_isolation.py tests/test_junit_report.py
  ```

### Gate D — Phase 2: Credible Evaluation & Scalable Execution
- **Scope**:
  - Reproducible end-to-end AIQA pipeline benchmark harness (`aiqa/benchmarks/harness.py`, `benchmarks/frozen_suite_v1.json`, `scripts/run_aiqa_benchmark.py`, `aiqa benchmark`).
  - Deterministic filtering & sharding (`--select`, `--tag`, `--shard 1/4`).
  - Bounded parallel workers (`--workers`) with isolated browser contexts.
  - Transient-only retries (`--retries`, `AttemptRecord`, `flaky` tracking).
  - Multi-shard report aggregation (`aiqa report --input ... --output-json ... --junit ... --html ...`).
- **Command**:
  ```bash
  pytest tests/test_phase2_scale_and_eval.py
  ```

### Gate E — Phase 3: Application Integration & Operating Model
- **Scope**:
  - Multi-role RBAC `storage_state` (`suite.roles`, `test.role`), cookie/JWT expiry checks (`validate_storage_state`), and `${ENV_VAR}` CI secret injection (`aiqa/auth/workflow.py`).
  - Explicit HTTP API setup/teardown fixtures (`FixtureSpec`, `FixtureLifecycleManager`), `{ENTITY_ID}` token binding, guaranteed LIFO teardown on failure, and `cleanup_errors` / `cleanup_failures` reporting (`aiqa/fixtures/lifecycle.py`).
  - Complex browser surfaces: `<iframe>` (`frame_selector`), open Shadow DOM piercing, OAuth/popup transitions (`PopupAction`), file upload (`UploadAction`), file download (`DownloadVerifier`), mobile viewports (`is_mobile=True`), and deterministic WCAG/ARIA accessibility checks (`AccessibilityVerifier`).
  - Enterprise governance: `OPERATING_MODEL.md`, `AuditLogger` (`aiqa/security/audit.py`), and `RetentionManager` (`aiqa/security/retention.py`, `aiqa retention`).
- **Command**:
  ```bash
  pytest tests/test_phase3_enterprise_app.py
  ```

---

## Step 3: Mandatory Documentation File Synchronization Checklist

Whenever **any** project change is made, inspect and update each of the following 6 files as needed:

| File | What Must Be Updated When Changes Occur |
| :--- | :--- |
| **`PROGRESS.md`** | 1. Update the top **Roadmap & Current Status** table (Stages 1–5 and Phases 0–3, including current total passing test count and lint status).<br>2. Add or update the corresponding `### 📍 Milestone N` section with date, problem solved, breakthrough modules, and verification metrics.<br>3. Keep the bottom `ENTERPRISE_PLAN.md` Phase 0–3 checklist accurate. |
| **`README.md`** | 1. Update **Quick Start** CLI examples if new commands/options are added.<br>2. Update **Performance evidence** if benchmarks are re-run.<br>3. Update **Architecture & Enterprise Readiness Status** table.<br>4. Update **Project Structure** tree whenever files/modules/skills are added, renamed, or removed. |
| **`USER_GUIDE.md`** | 1. Update **Install from a clean checkout** and verification commands.<br>2. Update **Core Commands Explained** table and CLI flag documentation (`auto`, `plan`, `test`, `coverage`, `report`, `benchmark`, `retention`).<br>3. Update **Enterprise CI Execution** examples if sharding, workers, retries, or governance options change. |
| **`ENTERPRISE_PLAN.md`** | 1. Keep the top **Implementation Status** table and **Completed Acceptance Checklist (Phases 0–3)** synchronized with the test files and implementation modules. |
| **`OPERATING_MODEL.md`** | 1. Update role-based authentication (`storage_state`), `${ENV_VAR}` secret injection, fixture lifecycle (`FixtureSpec`), complex browser surface schemas (`iframe`, Shadow DOM, popup, upload, download, mobile, `a11y`), or governance/retention/audit procedures whenever modified. |
| **`BENCHMARK_REPORT.md`** | 1. Update measured end-to-end benchmark metrics and baseline overhead ratios whenever `aiqa/benchmarks/harness.py` or `scripts/run_aiqa_benchmark.py` is run or modified. |

---

## Step 4: Run the Automated Verification & Doc Sync Script

Before finishing any task that modifies the repository, execute the bundled verification script:

```bash
python3 .agents/skills/aiqa-progress-tracker/scripts/verify_and_check_docs.py
```

This script:
1. Runs `ruff check .` and fails if any lint errors exist.
2. Runs `pytest -q` and parses the exact number of passing tests.
3. Checks that all 6 documentation files (`PROGRESS.md`, `README.md`, `USER_GUIDE.md`, `ENTERPRISE_PLAN.md`, `OPERATING_MODEL.md`, `BENCHMARK_REPORT.md`) exist, are non-empty, and contain up-to-date milestone and test-count references.
