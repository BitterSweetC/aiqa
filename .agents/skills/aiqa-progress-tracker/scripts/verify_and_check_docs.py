"""Automated verification and documentation synchronization check for AIQA.

Runs:
1. `ruff check .` (must pass with 0 errors)
2. `pytest -q` (must pass with 0 failures)
3. Verifies that all required project documentation files exist, are non-empty,
   and record the current passing test count and Phase 0-3 milestones.
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path


def _find_repo_root() -> Path:
    for candidate in [Path.cwd(), *Path(__file__).absolute().parents]:
        if (candidate / "pyproject.toml").exists() and (candidate / "aiqa").is_dir():
            return candidate
    raise RuntimeError("Could not locate AIQA repository root containing pyproject.toml")


REPO_ROOT = _find_repo_root()

REQUIRED_DOCS = [
    "PROGRESS.md",
    "README.md",
    "USER_GUIDE.md",
    "ENTERPRISE_PLAN.md",
    "OPERATING_MODEL.md",
    "BENCHMARK_REPORT.md",
    "AGENTS.md",
]


def run_step(label: str, cmd: list[str]) -> str:
    print(f"==> Running {label}: {' '.join(cmd)}")
    proc = subprocess.run(
        cmd,
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        print(f"[FAIL] {label} failed (exit {proc.returncode}):")
        print(proc.stdout)
        print(proc.stderr, file=sys.stderr)
        sys.exit(proc.returncode)
    print(f"[PASS] {label}")
    return proc.stdout + "\n" + proc.stderr


def main() -> int:
    # 1. Run Ruff
    run_step("Ruff Lint Check", [sys.executable, "-m", "ruff", "check", "."])

    # 2. Run Pytest
    pytest_out = run_step("Pytest Suite", [sys.executable, "-m", "pytest", "-q"])
    match = re.search(r"(\d+)\s+passed", pytest_out)
    if not match:
        print("[FAIL] Could not parse passing test count from pytest output.")
        return 1
    passed_count = int(match.group(1))
    print(f"==> Detected {passed_count} passing tests.")

    # 3. Verify documentation files exist and are synchronized
    missing_or_stale: list[str] = []
    for rel_path in REQUIRED_DOCS:
        doc_path = REPO_ROOT / rel_path
        if not doc_path.exists() or doc_path.stat().st_size == 0:
            missing_or_stale.append(f"{rel_path}: missing or empty")

    progress_text = (REPO_ROOT / "PROGRESS.md").read_text(encoding="utf-8")
    for milestone in [
        "Milestone 10",
        "Milestone 11",
        "Milestone 12",
        "Milestone 13",
        f"{passed_count}/{passed_count}",
    ]:
        if milestone not in progress_text:
            missing_or_stale.append(
                f"PROGRESS.md: missing expected reference '{milestone}' (update PROGRESS.md to match current progress/test count)"
            )

    enterprise_text = (REPO_ROOT / "ENTERPRISE_PLAN.md").read_text(encoding="utf-8")
    for phase_marker in ["Phase 0", "Phase 1", "Phase 2", "Phase 3"]:
        if phase_marker not in enterprise_text:
            missing_or_stale.append(f"ENTERPRISE_PLAN.md: missing '{phase_marker}'")

    if missing_or_stale:
        print("[FAIL] Documentation synchronization check failed:")
        for err in missing_or_stale:
            print(f"  - {err}")
        return 1

    print(
        f"[PASS] All {len(REQUIRED_DOCS)} documentation files are present and synchronized with {passed_count}/{passed_count} passing tests."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
