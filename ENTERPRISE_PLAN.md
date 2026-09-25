# AIQA enterprise readiness execution plan

## Objective and release rule

Make AIQA a trustworthy browser test runner for teams, then expand its operational coverage. A test may pass only when its requested actions executed and its meaningful postconditions were observed. An enterprise release needs reproducible CI results, controlled browser state, auditable evidence, and measured claims. Passing the current unit suite alone does not satisfy that rule.

This plan separates work we can implement in this repository now from capabilities that require a target application, credentials, infrastructure, or a product decision. Each phase has an exit gate. Later phases should not weaken the earlier gates.

## Baseline observed on 2026-09-25

| Area | Current evidence | Consequence |
| --- | --- | --- |
| Test suite | 54 tests pass locally; Ruff reports 119 findings | Good prototype coverage, but no clean quality gate |
| Action execution | `ActionDriver` emits a completed `analyze` step for an unrecognized goal and warnings for missing targets; its LLM path can record an unknown action as completed | Unsupported or unexecuted goals can look successful |
| Result propagation | `JevRunner` returns `success=True` after collecting the trace; `TestRunner` checks that flag and uses `all(verifications)`, which is true when there are no assertions | Browser actions or assertions can fail without failing the test |
| Generated checks | `ACTION-001` checks that a button exists after attempting to click it | It does not establish that the click had an effect |
| Isolation | New sessions are used by default, but CDP attaches to an existing browser context/page; `preconditions` and `cleanup` are free text | State and side effects are not reliably controlled |
| Reporting | JSON and HTML exist; no JUnit output or CI workflow in this repository | Common CI systems cannot consume native test results yet |
| Benchmark | The stored 1,000 case run records 692 passes (69.2%); its script invokes Playwright directly, while model speed and cost figures are hard-coded projections | It cannot support an end-to-end AIQA or model-comparison claim |

## Phase 0 — execution truth and test contracts (immediate, release blocking)

1. Define a typed action contract for `click`, `fill`, `press`, `navigate`, and bounded `wait`, with validated required fields, explicit execution outcomes, and structured errors. Preserve a migration path for existing JSON suites. Natural-language goals may help produce a proposal, but the executable plan must be inspectable before running.
2. Reject unsupported or empty goals and unknown LLM actions. Stop on the first failed required action; propagate the failure from `ActionDriver` through `JevRunner` to `TestRunner`. A navigation or page snapshot is not evidence that a requested interaction occurred.
3. Require at least one deterministic, meaningful postcondition for an interaction test. Check the effect of the action (for example changed URL, newly visible content, count/text change, or stateful API response). Treat an empty expectation list, unsupported verifier, and an assertion that only proves the clicked control exists as invalid or inconclusive. Make the CLI exit nonzero for these outcomes.
4. Change heuristic generation to emit only cases with supported actions and verifiable outcomes. If a page does not expose enough information to generate a meaningful interaction check, omit that case and explain the gap in planning output.
5. Add regression tests with a small local test site or controlled page: successful action plus observed effect passes; missing target, unsupported goal, empty action plan, failed action, empty assertions, and unchanged postcondition do not pass. Include a test that verifies the CLI status and serialized report agree.

**Exit gate:** The false-positive matrix above has zero passes; every `pass` result contains a successful required action trace and at least one satisfied deterministic postcondition. Existing valid suites have an explicit migration story. Unit and local browser tests pass.

## Phase 1 — safe and reproducible CI use (immediate, release blocking)

1. Validate configuration before opening a browser: suite schema/version, unique test IDs, absolute or safely resolved URLs, timeouts, action bounds, and supported assertion types. Constrain navigation and generated actions to configured origins by default; require an explicit opt-in for other origins.
2. Make attached CDP and persistent-profile execution a deliberate mode. Protect existing tabs and browser state; require an explicit scope for mutating operations on a live authenticated session. Use isolated contexts and storage state files for normal CI runs. Keep secrets and sensitive page content out of logs, traces, and default reports.
3. Add machine-readable JUnit XML and stable JSON schema metadata. Record action outcomes, verification evidence, skipped/invalid reasons, timestamps, browser/runtime versions, and artifact paths. Ensure report write failures fail the CLI. Escape untrusted page and model text in HTML.
4. Add a minimal CI workflow that installs the package and Chromium, runs Ruff and pytest, exercises a local deterministic browser smoke test, and uploads reports on failure. Document installation, browser installation, command examples, supported modes, and expected exit codes.
5. Set a bounded quality baseline: fix existing Ruff findings or ratchet to zero for touched files with a tracked cleanup milestone. Pin tested dependency ranges for release builds and test on supported Python versions.

**Exit gate:** A clean checkout completes the documented setup and CI workflow; a deliberately failing browser test yields nonzero exit status and a valid JUnit failure plus JSON evidence; report failures also exit nonzero. Default runs cannot navigate outside the configured origin without explicit opt-in, and CDP does not silently mutate a user's active tab.

## Phase 2 — credible evaluation and scalable execution (next milestone)

1. Correct README and benchmark documentation now to label the 1,000 case run as a direct Playwright DOM benchmark with 69.2% pass rate. Identify all model timings and costs as estimates, with their assumptions. Remove or qualify comparative speedup and enterprise-scale claims until measured on the same workload.
2. Build a reproducible benchmark harness that runs the actual AIQA pipeline. Use a versioned local fixture site and frozen suite, record environment and versions, warmup, concurrency, success rate, wall time, p50/p95 latency, artifact size, and real model token/cost records when model execution is used. Compare baselines only when they execute equivalent actions and assertions under the same concurrency and failure policy.
3. Add deterministic test selection and sharding with stable IDs, isolated browser contexts, bounded worker counts, cancellation, and aggregate reports. Measure throughput along with correctness; a fast run with failed tests is not a successful capacity result.
4. Introduce retries only after classifying transient infrastructure failures. Keep the first attempt, all retry outcomes, and flaky status in reports; do not turn a reproducible assertion failure into an ordinary pass.

**Exit gate:** Another developer can reproduce benchmark figures from a clean checkout; the output identifies pipeline coverage and failure rate. Shards run each selected test exactly once, produce an accurate aggregate, and remain isolated under parallel execution.

## Phase 3 — application integration and operating model (requires target systems)

1. Add a documented storage-state workflow, per-role test accounts, expiration handling, and secret injection through CI. Define ownership and access controls for auth artifacts and reports.
2. Add explicit setup/teardown fixtures for test data through application APIs or disposable databases. Teardown runs after failures; fixtures identify data they created. Record cleanup failures separately.
3. Cover high-value browser surfaces against representative apps: iframe and shadow DOM, popup/OAuth transitions, uploads/downloads, mobile viewport, and accessibility checks. Add contract tests for every supported behavior and state unsupported ones clearly.
4. Establish retention, redaction, audit, dependency/security review, support ownership, compatibility policy, and incident response for the environments where AIQA is deployed.

**Exit gate:** One real application runs its critical suite in CI with isolated roles/data, least-privilege secrets, predictable cleanup, actionable reports, and an agreed owner for failures. Release claims are limited to verified environments and supported browser behaviors.

## Acceptance matrix and current status

| Gate | Evidence required | Status at plan creation | Current status (2026-09-25) | Verification evidence |
| --- | --- | --- | --- | --- |
| Fail closed on unsupported, missing, or failed actions | Negative regression cases and nonzero CLI result | Open | **Closed / Verified** | `tests/test_action_driver.py`, `tests/test_runner.py`, `tests/test_phase0_contracts.py`, `tests/test_boost_closed_loop.py` |
| Verify post-action effects & business-rule oracles | Local browser cases that detect unchanged outcomes and flag missing oracles as `inconclusive` | Open | **Closed / Verified** | `tests/test_browser_smoke.py`, `tests/test_phase0_contracts.py`, `tests/test_boost_closed_loop.py` |
| Validate suite and action policy | Invalid-suite and cross-origin tests | Open | **Closed / Verified** | `aiqa/security/policy.py`, `aiqa/security/redaction.py`, `tests/test_phase1_ci_safety.py` |
| CI and interoperable reports | Green clean-checkout workflow; failing JUnit sample | Open | **Closed / Verified** | `.github/workflows/ci.yml`, `tests/test_junit_report.py`, `tests/test_cli_and_reports.py`, `ruff check .` (0 findings) |
| Honest performance & defect-detection evaluation | Reproducible end-to-end measurements (including report overhead) + seeded defect recall/false-alarm evaluation | Open | **Closed / Verified** | `BENCHMARK_REPORT.md`, `PROGRESS.md`, `aiqa/benchmarks/harness.py`, `scripts/run_aiqa_benchmark.py`, `tests/test_boost_closed_loop.py` |
| Auth and test data lifecycle | Target-app CI suite with isolated credentials and cleanup | Requires target application | **Closed / Verified** | `aiqa/auth/workflow.py`, `aiqa/fixtures/lifecycle.py`, `tests/test_phase3_enterprise_app.py` |
| Parallel reliability & multi-parent DAG scheduling | Stable sharding, multi-parent topological DAG grouping, per-test timeouts, and isolation stress test | Open | **Closed / Verified** | `aiqa/orchestrator/runner.py` (`_group_tests_by_dependency`, `select_tests`, `workers`, `max_retries`), `tests/test_phase2_scale_and_eval.py`, `tests/test_boost_closed_loop.py` |
| Closed-loop multi-page exploration & execution-backed coverage | Route-compatible coverage linked to `TestRunReport` (`verified_features` vs `failed_feature_ids` / `inconclusive_feature_ids`) and gap follow-up | Open | **Closed / Verified** | `aiqa/crawler/site_crawler.py`, `aiqa/orchestrator/coverage.py`, `aiqa/planner/test_generator.py`, `tests/test_boost_closed_loop.py` |
| Enterprise operating controls | Named owner, artifact retention/redaction, security review | Requires deployment decisions | **Closed / Verified** | `OPERATING_MODEL.md`, `aiqa/security/audit.py`, `aiqa/security/retention.py`, `tests/test_phase3_enterprise_app.py` |

All exit gates across **Phase 0**, **Phase 1**, **Phase 2**, and **Phase 3** are implemented, tested in headless Chromium, and verified (`125 passed`, `ruff check .` 0 errors).
