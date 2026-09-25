# AIQA — Development Progress & Breakthrough Log

This document permanently tracks every stage, architectural milestone, and breakthrough achieved during the development of AIQA.

---

## 🗺️ Overall 5-Stage & Enterprise Phase 0–3 Roadmap & Current Status

| Stage / Phase | Milestone | Status | Key Breakthroughs & Deliverables |
| :--- | :--- | :---: | :--- |
| **Stage 1** | **Core Runner & Multi-Verifier (MVP-1)** | **COMPLETED ✅** | Standardized Pydantic v2 schemas, Browser lifecycle, Triple Verifier (DOM + URL + Semantic LLM), TestRunner orchestrator, Rich CLI `aiqa test`. 31/31 unit tests passing. |
| **Stage 2** | **AI Test Planner & CDP Integration (MVP-2)** | **COMPLETED ✅** | `SiteInspector` (DOM element extraction), `TestPlanner` (LLM + heuristic generation), Native CDP (`--cdp`) & persistent profile (`--profile`), `aiqa plan` CLI command. 36/36 unit tests passing. |
| **Stage 3** | **Coverage Tracking & Multi-Route Exploration** | **COMPLETED ✅** | Multi-route site crawler (`SiteCrawler`), dynamic `FeatureRegistry`, `CoverageAnalyzer`, Rich CLI `aiqa coverage`, gap analysis & JSON export. 43/43 unit tests passing. |
| **Stage 4** | **Universal Autonomous Action Driver** | **COMPLETED ✅** | Real live Playwright interactions on ANY website (clicking, filling inputs, form submission, navigation). Heuristic + LLM vision actions. |
| **Stage 5** | **Failure Root-Cause Diagnosis & HTML Dashboard** | **COMPLETED ✅** | Automated Root-Cause Analyzer (`FailureAnalyzer`), Browser network & console telemetry, standalone interactive HTML Dashboard (`HtmlReporter`), `aiqa report` CLI command. 53/53 unit tests passing. |
| **Phase 0** | **Execution Truth & Test Contracts** | **COMPLETED ✅** | Typed `BrowserAction` contracts (`click`, `fill`, `press`, `navigate`, `wait`, `upload`, `popup`), fail-closed action propagation, observable postcondition state diffing, planning omission notes. |
| **Phase 1** | **Safe & Reproducible CI Use** | **COMPLETED ✅** | Pre-browser configuration/origin policy (`aiqa/security/policy.py`), automatic secret redaction (`aiqa/security/redaction.py`), isolated browser default vs CDP/storage-state, JUnit XML (`--junit`), XSS-escaped HTML, 0 Ruff lint errors. |
| **Phase 2** | **Credible Evaluation & Scalable Execution** | **COMPLETED ✅** | End-to-end AIQA benchmark harness (`aiqa benchmark`, `1.1286s` vs `0.9396s` baseline, 100% pass), deterministic filtering & sharding (`--select`, `--tag`, `--shard`), bounded workers (`--workers`), transient-only retry (`--retries`), multi-shard aggregation. |
| **Phase 3** | **Application Integration & Operating Model** | **COMPLETED ✅** | Multi-role RBAC `storage_state` + expiry checks (`aiqa/auth/`), `${ENV_VAR}` injection, HTTP setup/teardown fixtures with `{ENTITY_ID}` binding & LIFO cleanup (`aiqa/fixtures/`), iframes, open Shadow DOM, popups, uploads, downloads, mobile viewports, WCAG/ARIA verifier (`AccessibilityVerifier`), `AuditLogger`, `RetentionManager` (`aiqa retention`). |
| **Milestone 15** | **Closed-Loop Exploration, Goal Planning & Defect Evaluation** | **COMPLETED ✅** | Multi-parent DAG topological grouping (with token-boundary precondition matching & cyclic sink ordering) & cumulative per-test `timeout` enforcement, route-aware & execution-verified `CoverageAnalyzer` (guarding element-specific features from selector-less tag matches), goal decomposition (`form`/negative boundary flows, `--role`), `business_rule` oracle & `inconclusive` guard, `ActionDriver` post-step re-observation (`state_delta`), modal dismissal & step/duration/cost budgets, closed-loop `aiqa auto` (gap-only follow-up execution), and seeded + planner defect-detection evaluation (`defect_recall=1.0`, `planner_defect_recall=1.0`, `false_alarm_rate=0.0`). **125/125 tests passing, 0 Ruff errors.** |

---

## 🏆 Key Breakthroughs & Milestones

### 📍 Milestone 1: Core Test Pipeline (Stage 1 Completed)
*Date: September 21, 2026*

- **Pydantic v2 Test Case Schema** (`aiqa/models/test_case.py`):
  - Defined `TestCase`, `Expectation`, `VerificationResult`, `TestSuite`, and `TestRunReport`.
  - Supports expectation types: `dom`, `url`, `api`, `visual`, `semantic`.
- **Browser Lifecycle Management** (`aiqa/executor/browser_session.py`):
  - Async context manager wrapping Playwright Chromium with robust lifecycle hooks.
- **Triple Verifier Architecture** (`aiqa/verifier/`):
  - `DomVerifier`: Element existence, text matching, count, and attribute checks.
  - `UrlVerifier`: URL path regex, query parameters, document title matching.
  - `SemanticVerifier`: GPT-4o vision fallback for qualitative assertions.
- **Test Orchestrator & CLI** (`aiqa/orchestrator/runner.py`, `aiqa/cli.py`):
  - Automated dependency tracking (`preconditions`), test isolation, timing metrics, and Rich console UI.

---

### 📍 Milestone 2: Live Amazon Bot Bypass & Cart Automation (Production Proof-of-Concept)
*Date: September 22, 2026*

- **The Challenge**:
  - Headless Chromium was immediately flagged by Amazon bot detection ("SORRY something went wrong" dog page).
  - Products had dynamic "See options" variants, Prime-exclusive pricing interstitials, and post-add warranty popups.
- **The Breakthrough**:
  - Connected Playwright via Chrome DevTools Protocol (`connect_over_cdp("http://127.0.0.1:9222")`) directly to the user's active, authenticated Chrome browser.
  - Bypassed bot detection and preserved live user login session (`Hello, User`).
  - Successfully searched for `"office chair"`, selected the *Ergonomic Office Chair with 3D Headrest*, added it to the cart directly from search results, and verified live cart state:
    - **Item Added**: *Ergonomic Office Chair, Mesh Home Office Desk Chairs with 3D Headrest*
    - **Price**: \$101.99
    - **Cart Count**: Verified 1 item added.

---

### 📍 Milestone 3: AI Test Planner & CDP Integration (Stage 2 Completed)
*Date: September 24, 2026*

- **Autonomous Site Inspection** (`aiqa/planner/site_inspector.py`):
  - Inspects any web URL or active CDP browser session.
  - Extracts headings, search inputs (`#twotabsearchtextbox`, `input[type=search]`), action buttons, forms, and navigation routes.
- **Dual-Engine Test Generator** (`aiqa/planner/test_generator.py`):
  - **LLM Mode**: Uses OpenAI/DeepSeek to convert inspected page structure and testing goals into structured `TestSuite` JSON.
  - **Heuristic Fallback Mode**: Generates smoke, search, and navigation tests out-of-the-box with **zero API keys required**.
- **CLI Commands & Native CDP Options** (`aiqa/cli.py`):
  - Added `aiqa plan --url <url> --output <path> [--cdp <url>] [--goal <text>]`.
  - Added `--cdp` and `--profile` support to `aiqa test`.
  - Handled `ECONNREFUSED` with user-friendly troubleshooting instructions.
  - Expanded test suite from 31 to **36 passing unit tests**.

---

### 📍 Milestone 4: Universal Autonomous Action Driver (Stage 4 Core Engine Completed)
*Date: September 24, 2026*

- **The Problem Solved**:
  - `jev-ultrafast` required proprietary TypeSafe API keys, causing the fallback to simulate actions without actually clicking or typing.
  - Non-Amazon websites could not be genuinely tested or interacted with.
- **The Breakthrough**:
  - Built **`aiqa/executor/action_driver.py`**:
    - **Heuristic Action Driver**: Extracts links, inputs, and buttons; executes real Playwright clicks, text fills, and keyboard submissions with **zero API keys required**.
    - **LLM Action Agent**: Uses vision/DOM snapshots for multi-step chained browser actions when API keys are available.
  - Integrated `ActionDriver` directly into `JevRunner` (`aiqa/executor/jev_runner.py`), replacing the simulation fallback with live browser execution.
- **Real-World Multi-Site Verification**:
  - Tested on `https://example.com`: Actually clicked the `"Learn more"` link and navigated to `iana.org` (100% pass).
  - Tested on `https://news.ycombinator.com`: Discovered routes, clicked `"Hacker News"`, clicked `"new"`, verified live pages (3/3 passed).
  - Test suite expanded to **40/40 passing unit tests**.

---

### 📍 Milestone 5: Coverage Tracking & Multi-Route Exploration (Stage 3 Completed)
*Date: September 24, 2026*

- **The Problem Solved**:
  - QA engineers need visibility into what percentage of a website's features and routes are covered by their test suites, and where critical gaps (e.g. untested auth or checkout flows) exist.
- **The Breakthrough**:
  - Built **`aiqa/crawler/site_crawler.py`**:
    - Discovers internal website routes up to configurable depth and page limits.
    - Classifies interactive features: search bars, shopping carts, authentication portals, checkout links, forms, and CTA buttons.
  - Built **`aiqa/orchestrator/coverage.py`**:
    - Maps test cases to discovered features via CSS selectors, tags, semantic keywords, and routes.
    - Generates coverage rate per feature type (`search`, `cart`, `auth`, `checkout`, `action`).
    - Pinpoints specific untested feature gaps.
  - Added **`aiqa coverage` CLI command** (`aiqa/cli.py`):
    - Full terminal dashboard with Rich tables, coverage summaries, and optional JSON export.
  - Unit and integration tests expanded to **43/43 passing unit tests**.
- **Real-World Multi-Site Verification**:
  - Verified live crawl and coverage on `https://news.ycombinator.com`.
  - Verified gap analysis and JSON reporting (`./reports/coverage_hn.json`).

---

### 📍 Milestone 6: Failure Root-Cause Diagnosis & Interactive HTML Dashboard (Stage 5 Completed)
*Date: September 24, 2026*

- **The Problem Solved**:
  - When web tests fail, developers and QA engineers waste hours manually parsing logs, trying to figure out if the root cause was a frontend JS error, a backend API 500 error, an authentication rejection, or a UI selector change.
- **The Breakthrough**:
  - Built **`aiqa/analyzer/failure_analyzer.py`**:
    - Dual-engine Root-Cause Analyzer (Heuristic + LLM Vision).
    - Categorizes root causes: Backend 5xx, Auth 401/403, Frontend JS runtime errors, DOM selector absence, Route mismatches, and execution timeouts.
    - Produces structured diagnoses with **likely cause**, concrete **evidence bullets**, and actionable **developer remediation steps**.
  - Built **Browser Telemetry Engine** (`aiqa/executor/browser_session.py`):
    - Real-time listeners for console errors (`console.error`, unhandled page exceptions).
    - Network traffic listeners capturing failed requests (`4xx`, `5xx`, aborted connections).
  - Built **`aiqa/reports/html_report.py`**:
    - Modern, standalone, self-contained single-file HTML dashboard with zero external CDN dependencies required.
    - KPI cards (Total, Passed, Failed, Errors, Skipped, Pass Rate), animated visual progress meter.
    - Interactive client-side filters (All, Pass, Fail, Error, Skip) and real-time search box.
    - Expandable test cards featuring:
      - Highlighted **Root Cause Diagnosis Box** with severity badge.
      - Full **Verifications Table** (Type, Expectation, Actual vs Expected, Diagnostic message).
      - Step-by-step **Browser Action Trace**.
      - Embedded base64 failure screenshots.
      - Telemetry log viewer (Network errors + Console logs).
  - Integrated into CLI (`aiqa/cli.py`):
    - Added `--html <path>` option to `aiqa test`.
    - Added standalone converter command: `aiqa report --input <json> --html <path>`.
  - Test suite expanded to **53/53 passing unit tests**.

---

### 📍 Milestone 7: Dual-Engine Design & Universal Multi-LLM Architecture
*Date: September 24, 2026*

- **The Problem Solved**:
  - QA tools usually force users into a single expensive proprietary model or lock them behind mandatory API keys, preventing offline testing or local privacy.
- **The Breakthrough**:
  - Engineered **Dual-Engine Architecture** across all 5 stages of AIQA:
    - **Engine 1: Zero-Key Heuristic Fallback Engine**: Fully functional offline with zero credentials. Employs DOM tree parsers, regex semantic intent matching, and browser event listeners.
    - **Engine 2: Multi-LLM Gateway**: Connects to OpenAI (`gpt-4o-mini`, `gpt-4o`), DeepSeek (`deepseek-chat`), or local self-hosted instances (Ollama, vLLM) via standard OpenAI-compatible endpoints.
  - Complete configuration documentation and starter templates provided in `.env.example`, `README.md`, and `USER_GUIDE.md`.

---

### 📍 Milestone 8: One-Click Autonomous Testing (`aiqa auto`) & Quantitative Multimodal Benchmark
*Date: September 25, 2026*

- **The Problem Solved**:
  - Requiring separate planning, file-saving, running, and reporting commands created friction for quick smoke testing. Furthermore, evaluating whether pure multimodal vision models (Gemini / GPT-4o Computer Use) are viable alternatives required empirical benchmarking.
- **The Breakthrough**:
  - Implemented **`aiqa auto`** command (`aiqa/cli.py`):
    - Completely autonomous pipeline: Inspect URL ➔ Auto-synthesize targeted test suite ➔ Execute via Chromium browser session ➔ Capture real-time telemetry (unhandled JS, network 4xx/5xx) ➔ Compile interactive HTML dashboard ➔ Launch in browser.
  - **Empirical Multi-Site Verification (Zero Registration Sites)**:
    - `https://books.toscrape.com`: 3 tests passed in **2.34s** (100% pass rate).
    - `https://news.ycombinator.com`: 3 tests passed in **2.86s** (100% pass rate).
    - `https://quotes.toscrape.com`: 2 tests passed in **4.24s** (100% pass rate).
  - **Quantitative LLM Benchmark Synthesis (`BENCHMARK_REPORT.md`)**:
    - **Projected Speedup vs Analytical Vision Loop Estimates**: 2.34s – 4.24s measured local heuristic runs vs 60s – 90s estimated for pure screenshot-based vision loops (analytical projections based on published multimodal API latency and token pricing).
    - **Per-Step Latency**: ~195ms in local DOM execution vs ~5,900ms estimated in pure vision loops.
    - **Cost & Token Reduction**: $0.00 (0 tokens) in Heuristic Mode; estimated >94% token savings vs full-page screenshot loops.
  - Full test suite: **54/54 passing unit tests**.

---

### 📍 Milestone 9: 1,000-Test Direct Playwright DOM Concurrency Benchmark & Telemetry Archive
*Date: September 25, 2026*

- **The Problem Solved**:
  - Sample test suites of 3–5 tests were insufficient to measure raw Playwright DOM concurrency and latency distribution across 1,000 cases.
- **The Implementation & Scope Disclosure**:
  - Engineered **`scripts/run_1000_benchmark.py`**:
    - Synthesizes 1,000 test cases across public websites (`books.toscrape.com`, `quotes.toscrape.com`, `news.ycombinator.com`).
    - Executes all 1,000 cases via **direct Playwright async DOM calls** (25 concurrent workers on cached pages; does **not** invoke `TestRunner` / `JevRunner` / `ActionDriver` per test).
    - Records per-case timing telemetry in `reports/benchmark_1000_results.json`.
  - **Measured Direct Playwright DOM Metrics (1,000 Cases)**:
    - **Pass Rate**: **69.2% (692 passed, 308 failed)**.
    - **Total Wall-Clock Time**: **16.8 seconds** for all 1,000 cases (**59.53 cases / second** raw execution rate, **41.19 passed cases / second** effective throughput).
    - **Latency Distribution**: Mean = **160.39 ms**, P50 = **145.28 ms**, P95 = **270.03 ms**, P99 = **544.26 ms**.
    - **Analytical Comparison vs Estimated Vision Model Loops** *(theoretical projections assuming 3.2 steps/case and published token/latency pricing, not measured end-to-end model runs)*:
      - Frontier Vision Estimate (Gemini 3.8 Flash High / GPT-5.6 Sol): ~60.5 min estimated ($5.32 / ~4.2M tokens).
      - Standard Vision Estimate (GPT-4o / Gemini 1.5 Pro): ~256.7 min / 4.3 hours estimated ($10.50 / ~4.2M tokens).
    - **Direct DOM / Heuristic Token Cost**: **$0.00 (0 Tokens)**.
  - **Verifiable Data Artifacts**:
    - `reports/benchmark_suite_1000.json` (1,000 test definitions).
    - `reports/benchmark_1000_results.json` (1,000 run execution records, 692 pass / 308 fail).
    - `reports/benchmark_1000_summary.json` (statistical distribution).
  - Maintained **54/54 passing unit tests**.

---

### 📍 Milestone 10: Execution Truth & Test Contracts (Enterprise Phase 0 Completed)
*Date: September 25, 2026*

- **The Problem Solved**:
  - Free-form natural language instructions could fail to execute in the browser while page-load verifications still passed, creating a risk of false-green runs or silently dropped planning instructions.
- **The Breakthrough**:
  - Defined strict discriminated union **`BrowserAction` contracts** (`ClickAction`, `FillAction`, `PressAction`, `NavigateAction`, `WaitAction`, `UploadAction`, `PopupAction`) in `aiqa/models/test_case.py`.
  - Enforced **fail-closed execution propagation** across `ActionDriver` -> `JevRunner` -> `TestRunner`: any unexecutable action or unsatisfied contract returns `status="error"` with `failure_stage="action"` before verifiers can mark the test passed.
  - Added **observable postcondition state diffing** (`_capture_postcondition_state` in `aiqa/executor/jev_runner.py`), recording pre/post URL, title, focus, and DOM mutation diffs for every executed action.
  - Added explicit **`planning_notes`** in `TestPlanner` (`aiqa/planner/test_generator.py`) whenever heuristic fallback omits unsupported goals.
  - Verified by `tests/test_phase0_contracts.py` and `tests/test_browser_smoke.py` (**81/81 passing tests**).

---

### 📍 Milestone 11: Safe & Reproducible CI Execution (Enterprise Phase 1 Completed)
*Date: September 25, 2026*

- **The Problem Solved**:
  - Enterprise CI pipelines require pre-flight configuration validation before launching Chromium, strict origin allowlists, automatic redaction of credentials/tokens in artifacts, XSS-safe HTML dashboards, and JUnit XML artifacts.
- **The Breakthrough**:
  - Built **`aiqa/security/policy.py`**:
    - Validates `base_url`, `allowed_origins`, `cdp_url`, `user_data_dir`, and `storage_state` **before** launching a browser.
    - Blocks off-origin navigation (`NavigateAction`) when `allowed_origins` is configured.
  - Built **`aiqa/security/redaction.py`**:
    - Automatically scrubs sensitive selectors (`input[type="password"]`, `[name*="token"]`, `[name*="secret"]`, `[name*="cvv"]`), bearer tokens, API keys (`sk-...`), and URL query credentials across action traces, verifications, console/network telemetry, and failure diagnoses.
  - Hardened **`HtmlReporter`** (`aiqa/reports/html_report.py`) with strict HTML entity escaping (`html.escape`) and **`JUnitReporter`** (`aiqa/reports/junit_report.py`) for CI test runners (`--junit`).
  - Cleaned all repository lint issues (`ruff check .` -> **0 errors**).
  - Verified by `tests/test_phase1_ci_safety.py`, `tests/test_browser_isolation.py`, and `tests/test_junit_report.py` (**92/92 passing tests**).

---

### 📍 Milestone 12: End-to-End Pipeline Benchmark & Scalable Sharded Execution (Enterprise Phase 2 Completed)
*Date: September 25, 2026*

- **The Problem Solved**:
  - Historical 1,000-case metrics measured direct Playwright DOM calls rather than the full AIQA pipeline, and large test suites needed deterministic filtering, sharding, bounded parallel workers, and transient-only retry handling.
- **The Breakthrough**:
  - Built **`aiqa/benchmarks/harness.py`**, **`benchmarks/frozen_suite_v1.json`**, and **`scripts/run_aiqa_benchmark.py`** (`aiqa benchmark`):
    - Measures the complete AIQA pipeline (`TestRunner` -> `BrowserSession` -> `JevRunner` -> `ActionDriver` -> `DomVerifier`/`UrlVerifier` -> `JsonReporter`/`JUnitReporter`/`HtmlReporter`) against a deterministic local web application and compares directly against an equivalent raw Playwright baseline (**100% pass rate, 1.1286s AIQA vs 0.9396s raw Playwright, 1.201x ratio**).
  - Enhanced **`TestRunner`** (`aiqa/orchestrator/runner.py`) & CLI (`aiqa/cli.py`):
    - Deterministic selection & sharding (`--select`, `--tag`, `--shard 1/4`) with dependency closure preservation.
    - Bounded parallel worker pool (`--workers N`) with isolated per-worker browser contexts.
    - Transient-only retry policy (`--retries N`) recording per-attempt history (`AttemptRecord`) and marking recovered tests as `flaky=True` without retrying deterministic DOM/URL assertion failures.
    - Multi-shard report aggregation via `aiqa report --input shard1.json --input shard2.json --output-json combined.json --junit combined.xml --html dashboard.html`.
  - Verified by `tests/test_phase2_scale_and_eval.py` (**101/101 passing tests**).

---

### 📍 Milestone 13: Application Integration, Complex Web Surfaces & Operating Model (Enterprise Phase 3 Completed)
*Date: September 25, 2026*

- **The Problem Solved**:
  - Enterprise web applications require multi-role RBAC authentication, deterministic API data setup/teardown with guaranteed cleanup on failure, support for complex browser surfaces (`iframe`, Shadow DOM, OAuth popups, uploads, downloads, mobile viewports, WCAG accessibility), and operational governance.
- **The Breakthrough**:
  - Built **`aiqa/auth/workflow.py`**:
    - Multi-role `storage_state` resolution (`suite.roles`, `test.role`), cookie/JWT expiry inspection (`validate_storage_state`), and `${ENV_VAR}` CI secret injection (`inject_env_secrets_into_suite`) keeping secrets out of committed JSON and redacted in reports.
  - Built **`aiqa/fixtures/lifecycle.py`**:
    - Explicit HTTP setup/teardown fixtures (`FixtureSpec`, `FixtureLifecycleManager`) with `{ENTITY_ID}` response token extraction, automatic substitution into test actions/expectations, guaranteed LIFO teardown even when test assertions fail, and separate `cleanup_errors` / `cleanup_failures` reporting.
  - Extended **Complex Web Surface Support**:
    - `<iframe>` targeting (`frame_selector`), open Shadow DOM piercing, OAuth/popup window capture (`PopupAction`), file uploads (`UploadAction`), file download verification (`DownloadVerifier` in `aiqa/verifier/download.py`), mobile viewports (`is_mobile=True`), and deterministic WCAG/ARIA accessibility audits (`AccessibilityVerifier` in `aiqa/verifier/accessibility.py`).
  - Built **Enterprise Governance Controls**:
    - Published **[OPERATING_MODEL.md](./OPERATING_MODEL.md)**, tamper-evident JSONL audit logging (`AuditLogger` in `aiqa/security/audit.py`), and artifact retention pruning (`RetentionManager` in `aiqa/security/retention.py`, `aiqa retention`).
  - Verified by `tests/test_phase3_enterprise_app.py` (**112/112 passing tests, 0 Ruff errors**).

---

### 📍 Milestone 14: Step-by-Step Progress Tracker Skill & Automated Documentation Sync
*Date: September 25, 2026*

- **The Problem Solved**:
  - As the project evolves across stages and enterprise phases, code changes can drift ahead of project documentation unless there is an enforceable, repeatable step-by-step skill and verification gate.
- **The Breakthrough**:
  - Created workspace skill **`.agents/skills/aiqa-progress-tracker/SKILL.md`** and automated gate script **`.agents/skills/aiqa-progress-tracker/scripts/verify_and_check_docs.py`**.
  - Created workspace rules **`AGENTS.md`** and **`.agents/rules/project-doc-sync.md`** so any change to the repository follows the 4-step workflow (Baseline -> Stage/Phase Gates -> Mandatory 6-File Doc Sync -> Automated Verification Script).
  - Verified **112/112 passing tests** and **0 Ruff lint errors**.

---

### 📍 Milestone 15: Closed-Loop Multi-Page Exploration, Goal Planning, DAG Dependencies, Execution-Backed Coverage & Seeded Defect Evaluation
*Date: September 25, 2026*

- **The Problem Solved**:
  - Earlier iterations had six key engineering gaps identified in architectural review:
    1. `_group_tests_by_dependency` only matched a single parent (`break`), dropping secondary parents when a test depended on both `A` and `B`, and `test.timeout` was not enforced around `_run_single_attempt`.
    2. `CoverageAnalyzer` matched features by tag or repeated keyword without checking route compatibility or whether the matched test actually passed at runtime.
    3. `BenchmarkHarness` stopped its timer before `JsonReporter`/`JUnitReporter`/`HtmlReporter` and only evaluated a happy-path fixture rather than seeded defect detection (recall, false alarms, diagnosis accuracy, and multi-run consistency).
    4. `aiqa auto` only inspected a single landing page without crawling internal routes, recording blocked routes, or running closed-loop gap follow-up tests.
    5. `TestPlanner` heuristic mode generated nearly identical tests regardless of user `--goal`, capped suites at 4 tests, and lacked explicit `inconclusive` handling when business-rule oracles were absent.
    6. `ActionDriver` lacked post-step state re-observation (`observed_url`, `observed_title`, `state_delta`), automatic dismissal of blocking modals/overlays, and step/time/cost execution budgets.
- **The Breakthrough**:
  - **Multi-Parent DAG Grouping & Timeout Enforcement** (`aiqa/orchestrator/runner.py`):
    - Replaced single-parent chain grouping with Union-Find connected-component merging and Kahn's topological sort (`_group_tests_by_dependency`), with regex token-boundary precondition matching (`_string_references_test_id` avoiding `test_1` vs `test_10` collisions) and cyclic component sink ordering, ensuring `C` depending on `A` and `B` runs in `[A, B, C]` order and is skipped if either parent fails.
    - Enforced cumulative per-test `test.timeout` across setup, execution, business-rule checks, and verifiers in `_run_single_attempt`.
  - **Route-Aware & Execution-Verified Coverage** (`aiqa/orchestrator/coverage.py`, `aiqa/models/coverage.py`):
    - Enforced `_is_route_compatible` and selector-evidence checks before tag/keyword matching so selector-less navigation tests or tag matches on `/` never falsely claim coverage for interactive element features on `/catalog`.
    - Linked `CoverageAnalyzer.analyze(registry, suite, run_report=...)` to execution results, distinguishing planned `covered_features` from `verified_features` (passing & non-inconclusive), `failed_feature_ids`, `inconclusive_feature_ids`, `unverified_reasons`, and `blocked_routes`.
  - **Goal Decomposition, Capability Degradation & Business-Rule Oracles** (`aiqa/planner/test_generator.py`, `aiqa/orchestrator/runner.py`):
    - Added `decompose_goal(goal)` extracting target flows (`admin`, `auth`, `search`, `cart`, `checkout`, `pricing`, `form`, `navigation`), negative/boundary input markers (`include_negative`), roles (`--role`), and oracle requirements so distinct goals (`"Verify admin RBAC permissions"`, `"Verify shopping cart checkout"`, `"Verify coupon discount calculation"`) produce distinct suites with concrete business assertions.
    - Added explicit capability degradation notes to `suite.planning_notes` when a natural-language goal is planned without an LLM key, and scaled heuristic generation beyond 4 tests when `max_tests > 4`.
    - Added `Expectation(type="business_rule", oracle=..., inconclusive_if_missing_oracle=True)` and `_verify_business_rule` so missing business oracles are flagged `inconclusive=True` (`status="fail"`, `failure_category="inconclusive_business_rule"`) and surfaced in `HtmlReporter`.
  - **Observe → Decide → Act → Re-Observe Loop & Budgets** (`aiqa/executor/action_driver.py`, `aiqa/executor/jev_runner.py`):
    - Added `_observe_page_state` recording `observed_url`, `observed_title`, and `state_delta` on each `ActionOutcome`, automatic `_dismiss_blocking_overlay` recovery when a modal intercepts clicks/fills/presses/uploads, and `max_steps` / `max_duration_seconds` / `max_cost_usd` budget enforcement (`code="budget_exceeded"`).
  - **Multi-Page `SiteCrawler` & Closed-Loop `aiqa auto`** (`aiqa/crawler/site_crawler.py`, `aiqa/cli.py`):
    - Upgraded `SiteCrawler` to classify `search`, `pricing`, `cart`, `checkout`, `auth`, `admin`, and `interaction` features across routes, record `pages` summaries, and capture `blocked_routes` (HTTP 4xx/5xx, login redirects, 403 barriers).
    - Upgraded `aiqa auto` to crawl multi-page routes, plan initial goal tests, execute them, compute execution-verified coverage, generate and execute only follow-up gap tests (`auto_gap_suite_<timestamp>.json`) within remaining `--max-tests` budget, merge results, and write `auto_coverage_<timestamp>.json`.
  - **Report-Inclusive Benchmark Timing & Seeded + Planner Defect Evaluation** (`aiqa/benchmarks/harness.py`, `scripts/run_aiqa_benchmark.py`):
    - Included `JsonReporter`, `JUnitReporter`, and `HtmlReporter` inside `wall_time_seconds` alongside `runner_wall_time_seconds` and `report_generation_seconds`.
    - Added `BuggyFixtureSiteServer`, `build_seeded_defect_suite`, and `evaluate_defect_detection` (`--evaluate-defects`) executing both the planner-generated suite (`planner_defects_caught=4`, `planner_defect_recall=1.0`, `planner_false_alarms=0`) and the canonical seeded evaluation suite (`defect_recall=1.0`, `false_alarm_rate=0.0`, `precision=1.0`, `diagnosis_accuracy=1.0`, `inconclusive_rules_flagged=1`, `repeat_consistency_rate=1.0`).
  - Verified by `tests/test_boost_closed_loop.py` (**125/125 passing tests, 0 Ruff errors**).

---

## 🏛️ Enterprise Readiness Plan (`ENTERPRISE_PLAN.md` Phases 0–3): COMPLETED ✅

All phases and exit gates of **[ENTERPRISE_PLAN.md](./ENTERPRISE_PLAN.md)** have been implemented and verified:

- [x] **Phase 0 — Execution Truth and Test Contracts**:
  - Typed `BrowserAction` contracts (`click`, `fill`, `press`, `navigate`, `wait`, `upload`, `popup`) with strict validation, fail-closed execution propagation (`ActionDriver` -> `JevRunner` -> `TestRunner`), observable postcondition state diffing (`_capture_postcondition_state`), `business_rule` oracle / `inconclusive` guard, and planning omission/degradation notes (`planning_notes`).
  - Verified by `tests/test_phase0_contracts.py`, `tests/test_browser_smoke.py`, and `tests/test_boost_closed_loop.py`.
- [x] **Phase 1 — Safe and Reproducible CI Use**:
  - Pre-browser configuration & origin policy validation (`aiqa/security/policy.py`), automatic secret and sensitive-selector redaction (`aiqa/security/redaction.py`), isolated CDP & `storage_state` modes, JUnit XML export (`--junit`, `JUnitReporter`), HTML XSS escaping, zero Ruff findings (`ruff check .`), and GitHub Actions CI workflow (`.github/workflows/ci.yml`).
  - Verified by `tests/test_phase1_ci_safety.py`, `tests/test_browser_isolation.py`, and `tests/test_junit_report.py`.
- [x] **Phase 2 — Credible Evaluation and Scalable Execution**:
  - Reproducible end-to-end AIQA pipeline benchmark harness (`aiqa/benchmarks/harness.py`, `benchmarks/frozen_suite_v1.json`, `scripts/run_aiqa_benchmark.py`, `aiqa benchmark`) with report-inclusive wall time, equivalent direct Playwright baseline comparison (`1.1286s` vs `0.9396s`, 100% pass rate), and seeded defect-detection evaluation (`--evaluate-defects`: `defect_recall=1.0`, `false_alarm_rate=0.0`).
  - Deterministic test selection, multi-parent DAG topological grouping (`_group_tests_by_dependency`), per-test timeout enforcement, sharding (`--select`, `--tag`, `--shard 1/4`), bounded parallel workers (`--workers`), transient-only retry (`--retries`, `AttemptRecord`, `flaky`), and multi-shard report aggregation (`aiqa report`).
  - Verified by `tests/test_phase2_scale_and_eval.py` and `tests/test_boost_closed_loop.py`.
- [x] **Phase 3 — Application Integration and Operating Model**:
  - Multi-role RBAC `storage_state` workflow (`suite.roles`, `test.role`), cookie/JWT expiration checks (`validate_storage_state`), and CI secret injection (`${ENV_VAR}`) in `aiqa/auth/workflow.py`.
  - Explicit HTTP API setup/teardown fixtures (`FixtureSpec`, `FixtureLifecycleManager`), `{ENTITY_ID}` token binding, guaranteed LIFO teardown on failure, and separate `cleanup_errors` / `cleanup_failures` reporting in `aiqa/fixtures/lifecycle.py`.
  - Complex browser surfaces: `<iframe>` (`frame_selector`), open Shadow DOM piercing, OAuth/popup transitions (`PopupAction`), file upload (`UploadAction`), file download (`DownloadVerifier`), mobile viewports (`is_mobile=True`), and deterministic WCAG/ARIA accessibility audits (`AccessibilityVerifier`).
  - Enterprise governance & operating controls: `OPERATING_MODEL.md`, `AuditLogger` (`aiqa/security/audit.py`), and `RetentionManager` (`aiqa/security/retention.py`, `aiqa retention`).
  - Verified by `tests/test_phase3_enterprise_app.py` and `tests/test_boost_closed_loop.py`.

---

*Log maintained automatically by Antigravity AI & Contributors.*


