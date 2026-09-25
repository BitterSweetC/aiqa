# AIQA Repository Agent Rules (`AGENTS.md`)

When working in the AIQA repository, always follow these mandatory step-by-step rules:

1. **Activate the Step-by-Step Progress Skill**:
   - Read and follow `.agents/skills/aiqa-progress-tracker/SKILL.md` whenever making any changes to code, models, CLI commands, verifiers, security controls, benchmarks, or tests.

2. **Mandatory Project File Synchronization**:
   - Whenever you make **any** changes to the project, you **MUST** update the project documentation files step by step before completing your task:
     - `PROGRESS.md` — Update the top status table (including exact passing test count) and add/update the corresponding `### 📍 Milestone N` section and Phase 0–3 checklist.
     - `README.md` — Keep Quick Start commands, performance tables, Architecture & Enterprise Readiness table, and Project Structure tree synchronized with the repository.
     - `USER_GUIDE.md` — Keep installation steps, CLI command tables, flags, and enterprise examples synchronized with `aiqa/cli.py`.
     - `ENTERPRISE_PLAN.md` — Keep the Phase 0–3 status table and acceptance checklist synchronized with implementation and test suites.
     - `OPERATING_MODEL.md` — Keep RBAC `storage_state`, `${ENV_VAR}` secret injection, fixture lifecycle, complex surface schemas, and governance/audit/retention documentation synchronized.
     - `BENCHMARK_REPORT.md` — Keep end-to-end pipeline benchmark results synchronized whenever `aiqa/benchmarks/harness.py` or `scripts/run_aiqa_benchmark.py` is updated.

3. **Step-by-Step Verification Gate**:
   - Always run `python3 .agents/skills/aiqa-progress-tracker/scripts/verify_and_check_docs.py` (which executes `ruff check .`, `pytest -q`, and verifies documentation sync) and confirm 0 lint errors and 100% passing tests before finishing.
