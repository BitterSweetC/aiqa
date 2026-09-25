#!/usr/bin/env python3
"""Reproducible end-to-end AIQA pipeline benchmark runner.

Runs the full AIQA pipeline (`TestRunner` -> `BrowserSession` -> `JevRunner` -> `ActionDriver` ->
verifiers -> JSON/JUnit/HTML reporters) against the versioned local `FixtureSiteServer` and
frozen suite (`benchmarks/frozen_suite_v1.json`).
"""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path

from aiqa.benchmarks.harness import BenchmarkHarness


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run the reproducible end-to-end AIQA pipeline benchmark."
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=2,
        help="Bounded concurrent browser workers (1-32, default: 2)",
    )
    parser.add_argument(
        "--warmup",
        type=int,
        default=1,
        help="Number of warmup runs before measurement (default: 1)",
    )
    parser.add_argument(
        "--scale",
        type=int,
        default=1,
        help="Multiplier to replicate the frozen suite N times (default: 1)",
    )
    parser.add_argument(
        "--compare-baseline",
        action="store_true",
        help="Also run equivalent direct Playwright baseline under identical workload and concurrency",
    )
    parser.add_argument(
        "--evaluate-defects",
        action="store_true",
        help="Also run seeded defect-detection, false-alarm, diagnosis, and repeatability evaluation",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("./reports/benchmarks"),
        help="Output directory for benchmark artifacts (default: ./reports/benchmarks)",
    )
    args = parser.parse_args()

    harness = BenchmarkHarness(output_dir=args.output_dir, headless=True)
    report = asyncio.run(
        harness.run(
            workers=args.workers,
            warmup_runs=args.warmup,
            scale_multiplier=args.scale,
            compare_baseline=args.compare_baseline,
            evaluate_defects=args.evaluate_defects,
        )
    )
    print(json.dumps(report.model_dump(mode="json"), indent=2))
    return 0 if report.failed_tests == 0 and report.error_tests == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
