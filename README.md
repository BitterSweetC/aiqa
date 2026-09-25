# AIQA — AI-Powered Autonomous Website Testing

<p align="center">
  <img src="./assets/aiqa-icon.png" alt="AIQA icon" width="144" height="144">
</p>

An autonomous QA system that generates, executes, and verifies test cases using **Jev Ultrafast** for browser automation and **LLMs** for test planning and failure analysis.

## Architecture

```text
"Test login, search, and checkout flows" (--url + --goal + --role)
         ↓
   SiteCrawler & Inspector  ← crawls multi-page routes, forms & blocked routes (401/403/5xx)
         ↓
   QA Planner (LLM / Rule)  ← decomposes goal into flows, roles & business-rule oracles
         ↓
   DAG Test Runner          ← multi-parent topological scheduling, workers, retries & timeouts
         ↓
   ActionDriver (Closed-Loop) ← observe → act → dismiss overlays → re-observe (state_delta)
         ↓
   Multi-Verifier           ← DOM / URL / A11y / Download / Semantic / Business-Rule Oracles
         ↓
   PASS / FAIL / INCONCLUSIVE + Execution-Verified Coverage & Automatic Gap Follow-Up
         ↓
   Failure Analyzer         ← root-cause diagnosis + HTML / JUnit / JSON reports
```

## 📖 Beginner's Operating Manual

> 💡 **New to AIQA or web testing?**
> Read our step-by-step **[Beginner's Operating Guide (USER_GUIDE.md)](./USER_GUIDE.md)** to get started in under 3 minutes with zero coding required!

---

## ⚡ Quick Start

```bash
# 0. One-click closed-loop autonomous test (crawls routes, plans by goal, runs, fills coverage gaps & opens dashboard)
python3 -m aiqa.cli auto --url https://books.toscrape.com --goal "Test catalog search and navigation" --max-pages 5 --max-tests 5

# 1. Or auto-generate goal-driven test cases across site routes (supports --goal, --max-tests, --role)
python3 -m aiqa.cli plan --url https://example.com --goal "Verify core navigation and forms" --output ./sample_tests/my_tests.json

# 2. Run tests against a website and generate standalone HTML + JUnit XML reports
python3 -m aiqa.cli test --url https://example.com --tests ./sample_tests/my_tests.json --html ./reports/dashboard.html --junit ./reports/junit.xml

# 3. Check both planned and execution-verified feature coverage (plus blocked routes & gaps)
python3 -m aiqa.cli coverage --url https://example.com --tests ./sample_tests/my_tests.json --report ./reports/run_latest.json

# 4. Or attach to your active, logged-in Chrome browser via CDP (Amazon, etc.):
python3 -m aiqa.cli test --url https://www.amazon.com --tests ./sample_tests/amazon_chair.json --cdp http://127.0.0.1:9222
```

---

## Performance evidence

### 1. Reproducible End-to-End AIQA Pipeline Benchmark (`aiqa benchmark`)

The end-to-end benchmark harness (`aiqa/benchmarks/harness.py`, `scripts/run_aiqa_benchmark.py`) executes the full AIQA pipeline (`TestRunner` -> `BrowserSession` -> `JevRunner` -> `ActionDriver` -> `DomVerifier`/`UrlVerifier` -> `JsonReporter`/`JUnitReporter`/`HtmlReporter`) against a deterministic local fixture web application using the frozen suite `benchmarks/frozen_suite_v1.json`.

| Metric (`frozen_suite_v1`, `workers=2`, `warmup=1`) | AIQA End-to-End Pipeline | Direct Playwright Baseline |
| :--- | :--- | :--- |
| Pass rate | **100.0% (6 / 6 passed)** | **100.0% (6 / 6 passed)** |
| Wall-clock time | **1.1286 s** | **0.9396 s** |
| Latency (p50 / p95) | **370.0 ms / 387.5 ms** | — |
| Overhead ratio (postcondition diffing + reports + screenshots) | **1.201x** | 1.000x |

Reproduce from a clean checkout (including seeded defect-detection evaluation):
```bash
python3 scripts/run_aiqa_benchmark.py --workers 2 --warmup 1 --compare-baseline --evaluate-defects
```

Seeded defect-detection evaluation (`BuggyFixtureSiteServer`, `--evaluate-defects`):
- **Seeded defects caught / recall**: **4 / 4 (`100.0%` recall)** across broken search console error, dead cart button, HTTP 500 checkout gateway error, and HTTP 500 admin route.
- **Autonomous planner defects caught (`planner_defect_recall`)**: **4 / 4 (`100.0%` recall, `0` false alarms)** via end-to-end `SiteCrawler` -> `TestPlanner.generate_suite` -> `TestRunner`.
- **Healthy controls / false-alarm rate**: **2 / 2 passed (`0.0%` false-alarm rate, `100.0%` precision)**.
- **Root-cause diagnosis accuracy**: **`100.0%`** across all 4 seeded defects.
- **Missing-oracle business rule control**: **1 / 1 flagged `inconclusive=True`** (never falsely passed).

### 2. Historical 1,000-Case Direct Playwright DOM Benchmark

The checked-in 1,000-case result is a **direct Playwright DOM benchmark**, not an
end-to-end run through AIQA. The harness opens live public pages and performs
navigation and DOM assertions with 10 concurrent Playwright workers. It bypasses
AIQA's planner, action driver, verifier, orchestrator, and reporters.

| Stored run metric | Measured value |
| :--- | :--- |
| Cases | 1,000 |
| Passed / failed | 692 / 308 |
| Pass rate | **69.2%** |
| Wall-clock time | 16.8 seconds |
| Mean case latency | 160.39 ms |
| P95 case latency | 270.03 ms |

Earlier vision latency, token, and cost figures were estimates based on assumed per-step values; they were not measurements from equivalent workloads and are not presented as benchmark results. See [BENCHMARK_REPORT.md](./BENCHMARK_REPORT.md) for full methodology and reproduction details.

---

## 🏆 Architecture & Enterprise Readiness Status

Detailed milestone records are documented in **[PROGRESS.md](./PROGRESS.md)**, **[ENTERPRISE_PLAN.md](./ENTERPRISE_PLAN.md)**, and **[OPERATING_MODEL.md](./OPERATING_MODEL.md)**.

| Stage / Phase | Milestone | Status | Key Breakthroughs & Deliverables |
| :--- | :--- | :---: | :--- |
| **Stage 1–5** | **Core Runner, Planner, Crawler, ActionDriver & Dashboard** | **COMPLETED ✅** | Standardized Pydantic v2 schemas, Browser lifecycle, Triple Verifier, SiteCrawler, FailureAnalyzer, HTML Dashboard. |
| **Phase 0** | **Execution Truth & Test Contracts** | **COMPLETED ✅** | Typed `BrowserAction` contracts, fail-closed action execution, observable postcondition state diffing, `business_rule` oracle & `inconclusive` guard, planning omission/degradation notes. |
| **Phase 1** | **Safe & Reproducible CI Use** | **COMPLETED ✅** | Pre-browser suite/origin policy validation (`aiqa/security/policy.py`), automatic secret redaction (`aiqa/security/redaction.py`), JUnit XML (`--junit`), isolated CDP/storage-state modes, GitHub Actions CI (`.github/workflows/ci.yml`). |
| **Phase 2** | **Credible Evaluation & Scalable Execution** | **COMPLETED ✅** | End-to-end benchmark harness (`aiqa benchmark`) + seeded & planner defect evaluation (`--evaluate-defects`, `defect_recall=1.0`, `planner_defect_recall=1.0`), multi-parent DAG topological grouping, per-test timeouts, deterministic filtering & sharding (`--select`, `--tag`, `--shard`), bounded parallel workers (`--workers`), transient-only retry (`--retries`), multi-shard report aggregation (`aiqa report`). |
| **Phase 3** | **Application Integration & Operating Model** | **COMPLETED ✅** | Multi-role RBAC `storage_state` + expiry validation (`aiqa/auth/`), `${ENV_VAR}` CI secret injection, API setup/teardown fixtures with `{ENTITY_ID}` binding & guaranteed LIFO cleanup (`aiqa/fixtures/`), iframes, open Shadow DOM, popups, uploads, downloads, mobile viewports, WCAG/ARIA checks (`AccessibilityVerifier`), audit logging (`AuditLogger`), and artifact retention (`aiqa retention`). |
| **Milestone 15** | **Closed-Loop Exploration, Goal Planning & Execution-Backed Coverage** | **COMPLETED ✅** | Multi-page `SiteCrawler` with `blocked_routes`, goal decomposition (`decompose_goal`, `--role`), `ActionDriver` post-step re-observation (`state_delta`), modal dismissal & step/duration/cost budgets, execution-verified `CoverageAnalyzer` (`verified_features` vs `failed_feature_ids`/`inconclusive_feature_ids`), and closed-loop `aiqa auto` gap follow-up (**125/125 tests passing**). |

---

## 🤖 Dual-Engine Architecture & Supported AI Models

AIQA supports a built-in heuristic path and an optional LLM path. The heuristic
path needs no model API key; when used with complex natural-language goals, it
decomposes target flows (`auth`, `search`, `cart`, `checkout`, `admin`, `pricing`, `form`) and records explicit capability degradation notes or `inconclusive` flags when business-rule oracles are missing.

```text
┌────────────────────────────────────────────────────────────────────────┐
│                        AIQA Dual-Engine Core                           │
├───────────────────────────────────┬────────────────────────────────────┤
│  Mode 1: Built-in Heuristic Engine│  Mode 2: AI Generative LLM Engine  │
│  (100% Free • Zero API Key Needed)│  (Advanced Reasoning & Vision)     │
├───────────────────────────────────┼────────────────────────────────────┤
│ • Goal decomposition & DOM parser │ • OpenAI (gpt-4o, gpt-4o-mini)     │
│ • Closed-loop action & modal guard│ • DeepSeek (deepseek-chat)         │
│ • Rule-based failure diagnosis    │ • Local Ollama / vLLM / Self-host  │
│ • Multi-route & gap-fill suites   │ • Multi-step dynamic replanning    │
└───────────────────────────────────┴────────────────────────────────────┘
```

### Supported Providers & Models

| Provider | Supported Models | Configuration | Cost / Privacy |
| :--- | :--- | :--- | :--- |
| **OpenAI** *(Default)* | `gpt-4o-mini`, `gpt-4o` | `OPENAI_API_KEY=sk-...` | Best balance of speed & vision accuracy |
| **DeepSeek** | `deepseek-chat`, `deepseek-reasoner` | `OPENAI_BASE_URL=https://api.deepseek.com/v1`<br>`OPENAI_API_KEY=...`<br>`OPENAI_MODEL=deepseek-chat` | Extremely cost-effective |
| **Local Ollama** | `llama3`, `mistral`, `qwen2.5` | `OPENAI_BASE_URL=http://localhost:11434/v1`<br>`OPENAI_MODEL=llama3:latest` | 100% local, offline & private |
| **OpenAI-Compatible Gateways** | LiteLLM, OpenRouter, vLLM | `OPENAI_BASE_URL=...`<br>`OPENAI_API_KEY=...` | Flexible multi-model gateway |

### Configuration Options

1. **Via `.env` file (Recommended)**:
   ```bash
   cp .env.example .env
   # Edit with your API key and preferred model
   ```

2. **Via Shell Environment Variables**:
   ```bash
   export OPENAI_API_KEY="sk-..."
   export OPENAI_MODEL="gpt-4o-mini"
   ```

3. **Via CLI Argument Override**:
   ```bash
   python3 -m aiqa.cli plan --url https://example.com --model gpt-4o --output ./tests.json
   ```

---

## 📁 Project Structure

```text
aiqa/
├── aiqa/
│   ├── cli.py                  # CLI entry point (test, plan, coverage, report, benchmark, retention, auto)
│   ├── models/
│   │   ├── test_case.py        # Pydantic v2 schemas (TestCase, TestResult, FixtureSpec, AttemptRecord)
│   │   └── coverage.py         # FeatureRegistry & CoverageReport models
│   ├── auth/
│   │   └── workflow.py         # Role storage_state validation, expiry checks & CI ${ENV_VAR} injection
│   ├── fixtures/
│   │   └── lifecycle.py        # Setup/teardown HTTP fixtures, {ENTITY_ID} binding & guaranteed LIFO cleanup
│   ├── security/
│   │   ├── policy.py           # Pre-browser suite & origin policy enforcement
│   │   ├── redaction.py        # Automatic secret & sensitive selector redaction
│   │   ├── audit.py            # Tamper-evident JSONL audit logging
│   │   └── retention.py        # Artifact retention pruning manager
│   ├── benchmarks/
│   │   └── harness.py          # Reproducible end-to-end AIQA pipeline benchmark harness
│   ├── executor/
│   │   ├── browser_session.py  # Playwright browser manager, mobile/viewport & download listeners
│   │   ├── action_driver.py    # Action Driver (click, fill, press, navigate, wait, upload, popup, iframes)
│   │   └── jev_runner.py       # Execution coordinator & postcondition state diffing
│   ├── planner/
│   │   ├── site_inspector.py   # DOM interactive element extraction
│   │   └── test_generator.py   # LLM & heuristic test suite planner
│   ├── crawler/
│   │   └── site_crawler.py     # Multi-route exploration & feature classification
│   ├── verifier/
│   │   ├── dom.py              # DOM, iframe & open Shadow DOM assertions
│   │   ├── url.py              # URL & route regex assertions
│   │   ├── accessibility.py    # Deterministic WCAG/ARIA accessibility verifier
│   │   ├── download.py         # Deterministic file download verifier
│   │   └── semantic.py         # LLM vision qualitative assertions
│   ├── analyzer/
│   │   └── failure_analyzer.py # Root-cause diagnosis engine
│   ├── orchestrator/
│   │   ├── runner.py           # Test execution pipeline coordinator (sharding, workers, retries, fixtures)
│   │   └── coverage.py         # Feature coverage & gap analyzer
│   └── reports/
│       ├── json_report.py      # Structured JSON run reports
│       ├── junit_report.py     # CI-native JUnit XML reports
│       └── html_report.py      # Standalone interactive HTML dashboard
├── .agents/
│   ├── rules/
│   │   └── project-doc-sync.md # Workspace rule enforcing mandatory doc synchronization
│   └── skills/
│       └── aiqa-progress-tracker/
│           ├── SKILL.md        # Step-by-step progress & documentation sync skill
│           └── scripts/
│               └── verify_and_check_docs.py # Automated lint, pytest & doc sync gate
├── benchmarks/                 # Frozen benchmark suites (frozen_suite_v1.json)
├── assets/
│   └── aiqa-icon.png           # AIQA project icon
├── sample_tests/               # Example test suites (Amazon, HN, shopping, etc.)
├── tests/                      # Unit, contract, safety, scale, and enterprise integration tests (125 tests)
├── AGENTS.md                   # Repository-wide agent rules & doc sync contract
├── ENTERPRISE_PLAN.md          # Phase 0–3 enterprise readiness plan & acceptance matrix
├── OPERATING_MODEL.md          # Enterprise governance, RBAC, retention & incident response guide
├── BENCHMARK_REPORT.md         # End-to-end & direct Playwright benchmark methodology
├── USER_GUIDE.md               # Operating manual & CLI reference
└── PROGRESS.md                 # Permanent stage & breakthrough log
```

---

## 📄 License

MIT
