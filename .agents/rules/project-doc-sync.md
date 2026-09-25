---
description: Always update PROGRESS.md, README.md, USER_GUIDE.md, ENTERPRISE_PLAN.md, OPERATING_MODEL.md, and BENCHMARK_REPORT.md whenever making any changes to the AIQA project, following the aiqa-progress-tracker skill step by step.
---

# Mandatory Step-by-Step Progress & Documentation Synchronization Rule

Whenever any code, schema, CLI flag, verifier, fixture, security policy, benchmark, or test is added or modified in this workspace:

1. Follow the step-by-step workflow in `.agents/skills/aiqa-progress-tracker/SKILL.md`.
2. Update all affected project documentation files (`PROGRESS.md`, `README.md`, `USER_GUIDE.md`, `ENTERPRISE_PLAN.md`, `OPERATING_MODEL.md`, `BENCHMARK_REPORT.md`), ensuring the exact test count and milestone records in `PROGRESS.md` are current.
3. Run `python3 .agents/skills/aiqa-progress-tracker/scripts/verify_and_check_docs.py` to verify `ruff check .`, `pytest`, and documentation synchronization.
