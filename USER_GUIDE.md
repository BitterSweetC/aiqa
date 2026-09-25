# 🔰 AIQA Beginner's Operating Guide
### *Autonomous Web Testing Made Easy — Zero Coding Required*

Welcome to **AIQA**! This guide is written for anyone—whether you are a junior QA engineer, product manager, founder, or complete beginner ("green hand")—to start testing any website in **under 3 minutes**.

---

## Install from a clean checkout

AIQA supports Python 3.11 through 3.14. From the repository root:

```bash
python3 -m venv .venv
source .venv/bin/activate  # Windows PowerShell: .venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -e '.[dev]'
python -m playwright install chromium
```

Linux CI images may also need browser system packages. Install them with:

```bash
python -m playwright install --with-deps chromium
```

Confirm the checkout before testing a site:

```bash
ruff check .
pytest
python3 .agents/skills/aiqa-progress-tracker/scripts/verify_and_check_docs.py
```

The CI workflow runs full-repository `ruff check .` (0 lint errors), the complete unit/contract/safety/scale/enterprise/closed-loop test suite (`125 passed`), and the Chromium browser smoke test on Python 3.11 through 3.14.

---

## 💡 What is AIQA in Simple Terms?

Normally, testing a website requires writing hundreds of lines of code (Selenium, Cypress, Playwright). When buttons or layouts change, tests break.

**AIQA is an autonomous AI QA bot**:
1. You give it a website URL (e.g. `https://news.ycombinator.com` or `https://www.amazon.com`).
2. AIQA **looks at the page**, discovers links, search bars, and buttons.
3. It **writes the tests automatically** in plain English.
4. It **opens a real browser**, clicks links, types search queries, and tests forms.
5. If something breaks, it **tells you why** (backend crash, missing button, or JavaScript bug) and gives you a visual **HTML Dashboard**.

---

## ⚡ 10-Second One-Click Autonomous Test (Fastest Way!)

If you just want to test a website immediately without any manual files, use the **`auto`** command:

```bash
python3 -m aiqa.cli auto --url https://books.toscrape.com --max-pages 5
```

> 🎯 **What happens in one single command?**
> 1. AIQA crawls internal routes (`--max-pages`) and maps out interactive elements and any blocked routes (401/403/login walls).
> 2. It decomposes your `--goal` and writes a tailored test suite (smoke, navigation, search, cart/checkout/admin flows).
> 3. It launches Chromium, clicks through the site, and verifies results in real-time.
> 4. It computes execution-verified coverage against the discovered `FeatureRegistry` and automatically generates/runs follow-up gap tests if uncovered features remain within `--max-tests`.
> 5. It captures console errors, network failures, and produces an interactive HTML dashboard plus `auto_coverage_<timestamp>.json`.

---

## ⚡ Step-by-Step Workflow (For Custom Control)

If you prefer custom control over test suites, use the step-by-step commands:

### Step 1: Tell AIQA to inspect a website and write tests
Let's ask AIQA to write tests for Hacker News:
```bash
python3 -m aiqa.cli plan --url https://news.ycombinator.com --output ./sample_tests/hn_tests.json
```
> **What happened?** AIQA opened the site, scanned the buttons and links, and generated a complete test suite saved to `./sample_tests/hn_tests.json`.

---

### Step 2: Run the tests in a real browser
Now run the tests you just generated:
```bash
python3 -m aiqa.cli test --url https://news.ycombinator.com --tests ./sample_tests/hn_tests.json --html ./reports/dashboard.html --junit ./reports/junit.xml
```
> **What happened?** AIQA launched Chromium, physically clicked the links, checked that each page loaded correctly, and saved an interactive dashboard to `reports/dashboard.html`.

---

### Step 3: Check site coverage (Planned vs. Execution-Verified)
Check how much of the website is planned and verified by actual passing test execution:
```bash
python3 -m aiqa.cli coverage --url https://news.ycombinator.com --tests ./sample_tests/hn_tests.json --report ./reports/run_20260925_034555.json
```
> **What happened?** AIQA crawled the website's routes, matched your tests by route and selector, and—when `--report` is provided—verified which features actually passed at runtime vs. failed or remained inconclusive!

---

### Step 4: Open your visual dashboard
Open the generated report directly in your browser:
- On macOS:
  ```bash
  open ./reports/dashboard.html
  ```
- On Windows / Linux: Double-click `dashboard.html` in your file explorer.

You will see an executive dashboard with **Pass/Fail cards, action timelines, and failure diagnostics**!

---

## 🛠️ The 7 Core Commands Explained

| What You Want To Do | Command | In Plain English |
| :--- | :--- | :--- |
| **0. One-Click Auto Test** | `aiqa auto` | *"AI, crawl this site, write goal-driven tests, run them, close coverage gaps, and open the report!"* |
| **1. Generate Tests** | `aiqa plan` | *"AI, look at this website and write tests for me."* |
| **2. Run Tests** | `aiqa test` | *"AI, open a browser and run these tests (with optional sharding, workers, retries & JUnit)."* |
| **3. Check Coverage** | `aiqa coverage` | *"AI, crawl the site and show planned and execution-verified feature coverage & blocked routes."* |
| **4. Build / Aggregate Report** | `aiqa report` | *"AI, aggregate one or more JSON shard reports into HTML and JUnit XML."* |
| **5. Run E2E & Defect Benchmark** | `aiqa benchmark` | *"Measure end-to-end AIQA pipeline latency, baseline overhead, and seeded defect-detection recall."* |
| **6. Prune Old Artifacts** | `aiqa retention` | *"Delete expired report and screenshot artifacts based on age and count limits."* |

---

### Command 0: `aiqa auto` (One-Click Closed-Loop Autonomous Test)

```bash
# Basic closed-loop autonomous test:
python3 -m aiqa.cli auto --url https://news.ycombinator.com --max-pages 5

# With custom goal, role, or focus:
python3 -m aiqa.cli auto --url https://quotes.toscrape.com --goal "Verify author quotes and tag filters" --max-tests 5

# Watch the browser run visibly:
python3 -m aiqa.cli auto --url https://books.toscrape.com --no-headless
```

**Helpful Options**:
- `--goal "<text>"`: High-level testing focus (e.g., search, cart/checkout, admin RBAC, form validation).
- `--max-pages <number>`: Maximum internal routes to crawl for multi-page feature discovery (default: 5).
- `--max-tests <number>`: Maximum total test cases (initial + closed-loop gap follow-up) to generate and execute.
- `--role <role_name>`: Optional RBAC role name (`admin`, `guest`, `member`) to bind to generated test cases.

---

### Command 1: `aiqa plan` (Generate Tests)

```bash
# Basic test planning:
python3 -m aiqa.cli plan --url <WEBSITE_URL> --output <FILE_PATH.json>

# Give AI a specific testing goal and role:
python3 -m aiqa.cli plan --url https://www.amazon.com --goal "Search for office chair and verify cart" --output ./sample_tests/amazon_chair.json
```

**Helpful Options**:
- `--goal "<text>"`: Tell the AI what user journey to focus on (e.g., checkout flow, search, admin RBAC, discount rules, abnormal/boundary form inputs).
- `--max-tests <number>`: How many test cases to generate (default is 5; heuristic and LLM modes both scale with `--max-tests`).
- `--role <role_name>`: Bind generated tests to a specific RBAC role (`admin`, `guest`, `member`).

---

### Command 2: `aiqa test` (Execute Tests)

```bash
# Standard test run:
python3 -m aiqa.cli test --url <WEBSITE_URL> --tests <TEST_FILE.json>

# Watch the browser open visually (not hidden):
python3 -m aiqa.cli test --url <WEBSITE_URL> --tests <TEST_FILE.json> --no-headless

# Generate a standalone visual HTML dashboard:
python3 -m aiqa.cli test --url <WEBSITE_URL> --tests <TEST_FILE.json> --html ./reports/my_dashboard.html

# Generate HTML and CI-readable JUnit together:
python3 -m aiqa.cli test --url <WEBSITE_URL> --tests <TEST_FILE.json> \
  --html ./reports/my_dashboard.html --junit ./reports/junit.xml
```

Every run writes a versioned JSON report in `--output`. The JSON includes schema and runtime metadata, action outcomes, verification evidence, timestamps, and artifact paths. JUnit maps AIQA statuses as follows: `fail` becomes a `<failure>`, `error` becomes an `<error>`, and `skip` becomes `<skipped>`.

---

### Command 3: `aiqa coverage` (Route-Aware & Execution-Verified Feature Coverage)

```bash
python3 -m aiqa.cli coverage --url <WEBSITE_URL> --tests <TEST_FILE.json> [--report <RUN_REPORT.json>]
```
If you have untested features, failed features, inconclusive business rules, or blocked routes (e.g. HTTP 403 / login redirects), AIQA prints a detailed breakdown table:
```text
                  ⚠️ Untested Feature Gaps Detected
  Feature ID       Type      Untested Feature Name        Route
 ─────────────────────────────────────────────────────────────────────────────
  feat_auth_001    auth      Authentication: 'login'      https://site.com
```

---

### Command 4: `aiqa report` (Generate Dashboard Anytime)

If you already ran tests and have a JSON file in `./reports/`, you can turn it into an interactive HTML dashboard anytime:
```bash
python3 -m aiqa.cli report --input ./reports/run_20260925_034555.json --html ./reports/dashboard.html
```

### Browser modes

| Mode | Command option | Intended use |
| :--- | :--- | :--- |
| Isolated browser | Default | Local runs and CI with a clean, temporary browser context |
| Persistent profile | `--profile <directory>` | A dedicated automation profile whose stored state may be changed by tests |
| Attached browser | `--cdp <endpoint>` | An explicitly launched remote-debugging browser; tests can change its page and authenticated state |

Use the isolated default in CI. Treat profile directories and storage-state files as secrets: keep them outside source control and do not upload them with reports.

### Exit codes

| Code | Meaning |
| :--- | :--- |
| `0` | The command completed and the run has no failed or errored tests |
| `1` | Configuration, execution, verification, or report writing failed |
| `2` | The command line itself is invalid, such as a missing required option |

Both `test` and `auto` return nonzero when a JSON, HTML, or requested JUnit report cannot be written. This prevents CI from showing a successful job without its required evidence.

---

## 🔐 How to Test Websites That Require Login (e.g. Amazon, Google, etc.)

Websites like Amazon have anti-bot protections, CAPTCHAs, or 2-factor authentication (SMS / Authenticator app) that block automated headless browsers.

**AIQA solves this with Chrome DevTools Protocol (`--cdp`)**:
You log in manually once in your regular Chrome browser, and AIQA attaches directly to it!

### Step-by-Step CDP Instructions (macOS):

1. **Quit Chrome completely** (`Cmd + Q`).
2. **Launch Chrome in remote debugging mode**:
   ```bash
   open -na "Google Chrome" --args --remote-debugging-port=9222 --user-data-dir="/tmp/chrome_aiqa_profile" --no-first-run "https://www.amazon.com"
   ```
3. In the Chrome window that opens, **log into your account** (solve any CAPTCHA or 2FA).
4. Run AIQA with the `--cdp` flag:
   ```bash
   python3 -m aiqa.cli test --url https://www.amazon.com --tests ./sample_tests/amazon_chair.json --cdp http://127.0.0.1:9222 --html ./reports/amazon_report.html
   ```
5. **Done!** AIQA connects to your active Chrome browser, uses your active logged-in session (`"Hello, User"`), and runs the test without triggering bot shields.

---

## 🩺 How to Understand Test Failures

When a test fails, you don't need to read complicated stack traces. AIQA's **Failure Diagnostic Engine** explains the root cause in plain English:

### Example Failure in the Report:
```text
⚠️ Failure Root-Cause Diagnosis [Severity: MEDIUM]
• Summary: DOM verification failed: No element found matching selector '#checkout-btn'
• Likely Cause: Element Missing or State Mismatch for selector '#checkout-btn'
• Evidence: Expected selector '#checkout-btn' to match, observed value: 'None'
• 💡 Developer Remediation: Verify whether selector '#checkout-btn' exists, 
  is rendered conditionally, or requires waiting for asynchronous data fetch.
```

Common issues AIQA diagnoses automatically:
- **Backend 500 Error**: The server crashed when handling an API request.
- **Auth 401/403 Error**: You forgot to log in or your session expired.
- **Frontend JavaScript Error**: An unhandled `TypeError` crashed the page.
- **Missing Element**: A button or input was renamed or not displayed.

---

## ❓ Frequently Asked Questions (FAQ)

### 1. What AI does AIQA use? Do I need to provide an API key?
AIQA is built with a **Dual-Engine Architecture**:

- **Mode 1: Zero-Key Heuristic Engine (100% Free & Built-In)**
  - If you **do NOT provide an API key**, AIQA still works!
  - It uses built-in heuristic pattern matching to extract elements, click links, fill search inputs, run verifications, and diagnose backend/DOM failures.

- **Mode 2: AI LLM Mode (When you provide an API key)**
  - When an API key is set, AIQA upgrades to full generative intelligence for complex user flows, vision verification, and deep code root-cause diagnosis.
  - **Supported Models**:
    - **OpenAI**: `gpt-4o-mini` (default), `gpt-4o`
    - **DeepSeek**: `deepseek-chat` (super cheap and fast)
    - **Local / Custom**: Ollama, vLLM, or any OpenAI-compatible API.

**How to provide your API key**:
- In terminal:
  ```bash
  export OPENAI_API_KEY="sk-your-openai-key-here"
  ```
- Or using DeepSeek:
  ```bash
  export OPENAI_API_KEY="your-deepseek-key"
  export OPENAI_BASE_URL="https://api.deepseek.com/v1"
  export OPENAI_MODEL="deepseek-chat"
  ```
- Or simply create a `.env` file from `.env.example` in the project root!

### 2. What if I get `connect ECONNREFUSED 127.0.0.1:9222`?
This error only happens if you passed `--cdp http://127.0.0.1:9222` when Chrome is not running with remote debugging.
- **Solution 1**: Simply omit the `--cdp` flag, and AIQA will launch its own clean browser automatically.
- **Solution 2**: Launch Chrome using the command shown in the CDP section above before running the test.

### 3. Can I test local development websites (e.g. `localhost:3000`)?
**Yes!** Simply pass your local URL:
```bash
python3 -m aiqa.cli test --url http://localhost:3000 --tests ./sample_tests/shopping_site.json
```

### 4. How fast is AIQA compared with other browser agents?

Runtime depends on the target site, action count, browser startup, and whether a model is enabled. Use the benchmark harness for repeatable measurements in your environment. Treat figures based on different workloads or estimated model latency as projections rather than direct comparisons.

---

## ⚙️ Enterprise CI Execution: Sharding, Workers, Retries & Retention

### 1. Filter, Shard, and Parallelize Test Suites
```bash
# Run specific test IDs or tags across 4 parallel browser workers
aiqa test --url https://staging.example.com --tests ./suite.json \
  --tag smoke --workers 4 --retries 1 --junit ./reports/junit.xml

# Run shard 1 of 4 in CI matrix jobs
aiqa test --url https://staging.example.com --tests ./suite.json \
  --shard 1/4 --output-dir ./reports/shard1

# Aggregate multiple shard JSON reports into a unified dashboard + JUnit XML
aiqa report --input ./reports/shard1/report_1.json --input ./reports/shard2/report_2.json \
  --output-json ./reports/combined.json --junit ./reports/combined_junit.xml --html ./reports/dashboard.html
```

### 2. Reproducible End-to-End Benchmark & Artifact Retention
```bash
# Run the reproducible end-to-end AIQA pipeline benchmark
aiqa benchmark --workers 2 --warmup 1 --compare-baseline

# Prune expired reports and screenshots older than 30 days
aiqa retention --dir ./reports --max-age-days 30 --max-files 200
```

See **[OPERATING_MODEL.md](./OPERATING_MODEL.md)** for full details on role-based `storage_state`, `${ENV_VAR}` secret injection, API setup/teardown fixtures (`FixtureSpec`), iframes, open Shadow DOM, popups, file uploads/downloads, mobile viewports, and WCAG accessibility checks (`Expectation(type="a11y")`).

---

*Need help or want to customize test cases? Edit any JSON file in `./sample_tests/` or read `PROGRESS.md`, `ENTERPRISE_PLAN.md`, `OPERATING_MODEL.md`, and `BENCHMARK_REPORT.md` for architecture and benchmark details.*
