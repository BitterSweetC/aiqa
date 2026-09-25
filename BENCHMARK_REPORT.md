# AIQA benchmark evidence and methodology

*Recorded run date: September 25, 2026*

## What was measured

The stored 1,000-case run measured a standalone Playwright harness against three
live public sites. The harness navigates directly to pages and evaluates URL and
DOM assertions with 10 concurrent browser contexts.

It does **not** import or execute AIQA's planner, `ActionDriver`, `JevRunner`,
verifiers, orchestrator, or report generation. It also does not call a vision or
computer-use model. This result therefore describes direct Playwright checks,
not end-to-end AIQA performance and not a model comparison.

## Stored result

The checked-in summary reports:

| Metric | Measured value |
| :--- | :--- |
| Defined cases | 1,000 |
| Passed | 692 |
| Failed | 308 |
| Pass rate | **69.2%** |
| Wall-clock duration | 16.8 seconds |
| Aggregate test time | 160.39 seconds |
| Throughput | 59.53 cases/second |
| Mean case latency | 160.39 ms |
| Median case latency | 145.28 ms |
| P90 / P95 / P99 | 234.20 / 270.03 / 544.26 ms |

Passing 692 checks quickly does not make the 308 failures successful capacity.
The pass rate must be considered alongside throughput.

## Workload

The generator creates 1,000 definitions:

| Group | Count | Site | Check performed |
| :--- | ---: | :--- | :--- |
| Book categories | 50 | books.toscrape.com | Navigate, then check heading and URL |
| Catalog pages | 50 | books.toscrape.com | Navigate, then check product container and URL |
| Product checks | 400 | books.toscrape.com | Check price and availability text |
| Quote tags | 200 | quotes.toscrape.com | Check quote cards and tag URL |
| Hacker News routes | 200 | news.ycombinator.com | Check a page container and route URL |
| Quote-page anchors | 100 | quotes.toscrape.com | Check that top-tag anchors exist |

Despite names such as “interactive” in the historical dataset, the harness only
navigates to each `start_url` and evaluates assertions. It does not exercise an
AIQA natural-language goal or action plan.

## Reproduce the harness

From a clean checkout with Python 3.11 or newer:

```bash
python3 -m venv .venv
source .venv/bin/activate
python3 -m pip install -e '.[dev]'
python3 -m playwright install chromium
python3 scripts/run_1000_benchmark.py --concurrency 10 --output-dir reports
```

The run rewrites these artifacts:

- [benchmark_suite_1000.json](./reports/benchmark_suite_1000.json): generated definitions
- [benchmark_1000_results.json](./reports/benchmark_1000_results.json): per-case records
- [benchmark_1000_summary.json](./reports/benchmark_1000_summary.json): aggregate statistics

New output identifies the harness as `direct_playwright_dom`, records whether the
AIQA pipeline and model inference were exercised, captures worker concurrency,
and records Python, operating system, Playwright, and browser metadata.

### Reproduction limits

This is a live-site benchmark. Network conditions, remote content, throttling,
site availability, and target markup can change between runs. The suite and
dependency versions should be frozen before using results for release gates.
Browser version and hardware details should be recorded when publishing a run.

## Local component experiment

The separate [live benchmark experiment](./scripts/live_benchmark_experiment.py)
runs two local Playwright paths on one page:

1. a Playwright DOM locator click; and
2. a screenshot followed by coordinates obtained from a Playwright DOM bounding
   box, a Playwright mouse click, and a configured 600 ms delay.

Both paths use Playwright. The second path is a local coordinate simulation; it
does not use a model to inspect the screenshot or choose the coordinates. Its
delay is a harness setting. The experiment cannot establish vision-model latency,
accuracy, token usage, cost, or reliability.

## Status of prior comparison figures

Historical figures for vision latency, tokens, cost, pass rate, and ratios such
as 18x, 28x, 216.1x, and 916.8x were projections made from assumed per-step
values. No vision model ran the same cases under the same concurrency, action
semantics, assertions, timeouts, and failure policy. Those figures are estimates,
not empirical apples-to-apples results, and are excluded from current claims.

## Requirements for a valid comparison

A publishable AIQA performance comparison should:

1. Run the actual AIQA pipeline from suite input through serialized results.
2. Use a versioned local fixture site and a frozen suite so every runner receives
   equivalent pages, actions, and postconditions.
3. Apply the same concurrency, timeout, retry, isolation, and failure rules.
4. Record warmup policy, wall time, passed and failed counts, p50 and p95 latency,
   artifact sizes, software versions, hardware, and configuration.
5. Capture actual token and cost records when a model is invoked.
6. Repeat enough runs to report variance and investigate failed cases.

Until that experiment exists, these artifacts support only the narrower claim:
on one stored run, this direct Playwright harness completed 1,000 live-site checks
in 16.8 seconds and 692 checks passed.
