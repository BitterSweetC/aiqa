# AIQA — AI-Powered Autonomous Website Testing

An autonomous QA system that generates, executes, and verifies test cases using **Jev Ultrafast** for browser automation and **LLMs** for test planning and failure analysis.

## Architecture

```text
"Test my shopping website"
         ↓
    QA Planner (LLM)       ← designs test cases from source/URL
         ↓
   N structured tests
         ↓
       Jev Ultrafast        ← executes each test via browser
         ↓
     Verifier               ← deterministic + semantic checks
         ↓
   PASS / FAIL / coverage
         ↓
    Failure Analyzer (LLM)  ← explains why tests failed
```

## 📖 Beginner's Operating Manual

> 💡 **New to AIQA or web testing?**
> Read our step-by-step **[Beginner's Operating Guide (USER_GUIDE.md)](./USER_GUIDE.md)** to get started in under 3 minutes with zero coding required!

---

## ⚡ Quick Start

```bash
# 0. One-click autonomous test (Inspects site, generates tests, runs them & opens dashboard!)
python3 -m aiqa.cli auto --url https://books.toscrape.com

# 1. Or auto-generate custom test cases by inspecting any website with AI
python3 -m aiqa.cli plan --url https://example.com --output ./sample_tests/my_tests.json

# 2. Run tests against a website and generate a standalone HTML dashboard
python3 -m aiqa.cli test --url https://example.com --tests ./sample_tests/my_tests.json --html ./reports/dashboard.html

# 3. Check website feature test coverage and uncover gaps
python3 -m aiqa.cli coverage --url https://example.com --tests ./sample_tests/my_tests.json

# 4. Or attach to your active, logged-in Chrome browser via CDP (Amazon, etc.):
python3 -m aiqa.cli test --url https://www.amazon.com --tests ./sample_tests/amazon_chair.json --cdp http://127.0.0.1:9222
```

---

## Performance evidence

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

The result shows the throughput and failure rate of that specific harness against
live sites on one recorded run. It does not establish AIQA pipeline throughput or
an apples-to-apples advantage over a vision or computer-use agent. Earlier vision
latency, token, and cost figures were estimates based on assumed per-step values;
they were not measurements from equivalent workloads and are not presented as
benchmark results.

Artifacts and reproduction:

- [1,000 case definitions](./reports/benchmark_suite_1000.json)
- [1,000 individual results](./reports/benchmark_1000_results.json)
- [Stored summary](./reports/benchmark_1000_summary.json)
- Run `python3 scripts/run_1000_benchmark.py --concurrency 10` after installing
  the project and Playwright Chromium. Live-site and network changes may produce
  different results.

See [BENCHMARK_REPORT.md](./BENCHMARK_REPORT.md) for methodology, limitations,
and the requirements for a valid end-to-end comparison.

---

## 🏆 5-Stage Architecture & Status

Detailed milestone records and breakthroughs are permanently documented in **[PROGRESS.md](./PROGRESS.md)**.

| Stage | Milestone | Status | Key Breakthroughs & Deliverables |
| :--- | :--- | :---: | :--- |
| **Stage 1** | **Core Runner & Multi-Verifier** | **COMPLETED ✅** | Standardized Pydantic v2 schemas, Browser lifecycle, Triple Verifier (DOM + URL + Semantic LLM), TestRunner orchestrator, Rich CLI `aiqa test`. |
| **Stage 2** | **AI Test Planner & CDP Integration** | **COMPLETED ✅** | `SiteInspector` (DOM extraction), `TestPlanner` (LLM + heuristic generation), Native CDP (`--cdp`) & persistent profile (`--profile`), `aiqa plan` CLI. |
| **Stage 3** | **Coverage Tracking & Multi-Route Exploration** | **COMPLETED ✅** | Multi-route crawler (`SiteCrawler`), dynamic `FeatureRegistry`, `CoverageAnalyzer`, Rich CLI `aiqa coverage`, gap analysis & JSON export. |
| **Stage 4** | **Autonomous Action Driver** | **COMPLETED ✅** | Playwright clicks, text fills, and keyboard interactions for currently supported goal patterns, with optional LLM action generation. |
| **Stage 5** | **Failure Root-Cause Diagnosis & HTML Dashboard** | **COMPLETED ✅** | Automated Root-Cause Analyzer (`FailureAnalyzer`), Browser network & console telemetry, standalone interactive HTML Dashboard (`HtmlReporter`), `aiqa report` CLI command. |
| **One-Click** | **Autonomous Engine & Live-Site Trials** | **COMPLETED ✅** | `aiqa auto` inspection, execution, and HTML reporting; exercised on `quotes.toscrape`, `news.ycombinator`, and `books.toscrape`. |

---

## 🤖 Dual-Engine Architecture & Supported AI Models

AIQA supports a built-in heuristic path and an optional LLM path. The heuristic
path needs no model API key; its supported goals are narrower than the LLM path.

```text
┌────────────────────────────────────────────────────────────────────────┐
│                        AIQA Dual-Engine Core                           │
├───────────────────────────────────┬────────────────────────────────────┤
│  Mode 1: Built-in Heuristic Engine│  Mode 2: AI Generative LLM Engine  │
│  (100% Free • Zero API Key Needed)│  (Advanced Reasoning & Vision)     │
├───────────────────────────────────┼────────────────────────────────────┤
│ • Regex & DOM AST tree parser     │ • OpenAI (gpt-4o, gpt-4o-mini)     │
│ • Heuristic action driver         │ • DeepSeek (deepseek-chat)         │
│ • Rule-based failure diagnosis    │ • Local Ollama / vLLM / Self-host  │
│ • Smoke & route navigation suites │ • Qualitative visual verification  │
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
│   ├── cli.py                  # CLI entry point (test, plan, coverage, report)
│   ├── models/
│   │   ├── test_case.py        # Pydantic v2 schemas (TestCase, TestResult, FailureDiagnosis)
│   │   └── coverage.py         # FeatureRegistry & CoverageReport models
│   ├── executor/
│   │   ├── browser_session.py  # Playwright browser manager & telemetry listeners
│   │   ├── action_driver.py    # Universal Action Driver (heuristic + LLM vision)
│   │   └── jev_runner.py       # Execution coordinator
│   ├── planner/
│   │   ├── site_inspector.py   # DOM interactive element extraction
│   │   └── test_generator.py   # LLM & heuristic test suite planner
│   ├── crawler/
│   │   └── site_crawler.py     # Multi-route exploration & feature classification
│   ├── verifier/
│   │   ├── dom.py              # DOM element/text/count assertions
│   │   ├── url.py              # URL & route regex assertions
│   │   └── semantic.py         # LLM vision qualitative assertions
│   ├── analyzer/
│   │   └── failure_analyzer.py # Root-cause diagnosis engine
│   ├── orchestrator/
│   │   ├── runner.py           # Test execution pipeline coordinator
│   │   └── coverage.py         # Feature coverage & gap analyzer
│   └── reports/
│       ├── json_report.py      # Structured JSON run reports
│       └── html_report.py      # Standalone interactive HTML dashboard
├── sample_tests/               # Example test suites (Amazon, HN, shopping, etc.)
├── tests/                      # Unit and integration tests
├── USER_GUIDE.md               # Beginner's operating manual
└── PROGRESS.md                 # Permanent stage & breakthrough log
```

---

## 🔮 Enterprise Evolution Roadmap

AIQA is evolving from a smoke-testing copilot toward a framework that can meet
team CI and operational requirements. The open work below is required before
making enterprise-readiness claims for a deployment:

- [ ] **Pillar 1: CI/CD & Pipeline Native**: JUnit XML / Allure export, Slack/Feishu failure alert bots, distributed multi-node sharding (`--shard 1/4`).
- [ ] **Pillar 2: Headless Auth & State Factory**: Playwright `storageState.json` automated injection, direct API token minting (bypassing 2FA in CI).
- [ ] **Pillar 3: Deep Component Penetration**: Cross-domain `<iframe>` (Stripe/PayPal), Shadow DOM micro-frontends, multi-window OAuth redirects, and file upload/download assertions.
- [ ] **Pillar 4: Test Data Lifecycle & Fixtures**: Programmatic API & DB setup/teardown fixtures (creating test entities and auto-rollback), automatic retry with exponential backoff (`--retries 2`).

Detailed implementation details are tracked in **[PROGRESS.md](./PROGRESS.md)**.

---

## 📄 License

MIT
