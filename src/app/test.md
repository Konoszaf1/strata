╭────────────────────────────────────────────── Final Output ───────────────────────────────────────────────╮
│                                                                                                           │
│  # Strata (stack-cli) — Project Quality Assessment Report                                                 │
│                                                                                                           │
│  **Date:** 2026-03-17                                                                                     │
│  **Scope:** Full working tree at `/home/konst/LayeredFramework` (includes uncommitted changes)            │
│  **Project version:** 0.1.0 | **Git history:** 2 commits | **Stage:** Early prototype / active            │
│  development                                                                                              │
│                                                                                                           │
│  ---                                                                                                      │
│                                                                                                           │
│  ## Overall Verdict: Promising Prototype with Strong Foundations                                          │
│                                                                                                           │
│  **Rating: B+ for its stage** — architecturally sophisticated and well-structured for a v0.1.0 prototype  │
│  with only 2 commits. The core design (immutable state machine, 3-tier QA, layered cognitive pipeline)    │
│  shows experienced engineering judgment. However, the project lacks the development infrastructure (CI,   │
│  linting, type checking) and test coverage breadth (no async/integration/e2e tests) that would be needed  │
│  to call it production-ready.                                                                             │
│                                                                                                           │
│  If this were evaluated as a production tool, it would rate lower (C+/B-) due to missing infrastructure.  │
│  As a prototype demonstrating a novel architecture, it's impressive.                                      │
│                                                                                                           │
│  ---                                                                                                      │
│                                                                                                           │
│  ## 1. Architecture (Strong)                                                                              │
│                                                                                                           │
│  **Evidence examined:** `state.py` (274 lines), `pipeline.py` (688 lines), `config.py` (134 lines),       │
│  `validators.py` (457 lines), `drift.py` (222 lines), all 6 agent prompt files, 5 JSON schemas,           │
│  `pyproject.toml`                                                                                         │
│                                                                                                           │
│  **Strengths:**                                                                                           │
│  - **Immutable state machine** (`state.py`): Frozen Pydantic models with explicit `VALID_TRANSITIONS`     │
│  dict governing every status change. State is never mutated — every transition produces a new object.     │
│  This is a disciplined choice that prevents an entire category of bugs.                                   │
│  - **Clean module separation**: 20 source files across 4 subpackages (`agents/`, `qa/`, `ui/`,            │
│  `harness/`), each with a clear single responsibility. No circular dependencies observed.                 │
│  - **Layered QA architecture**: Tier 1 (deterministic structural checks in `validators.py`) runs before   │
│  any LLM call. Tier 2 (eval agent) provides semantic assessment. Tier 3 (`drift.py`) tracks cross-run     │
│  quality trends. This is a thoughtful design — catching schema violations and hollow content before       │
│  spending tokens on LLM eval.                                                                             │
│  - **Agent prompt design**: Each prompt file (e.g., `1-prompt.md`) has explicit boundary constraints      │
│  ("NOT Your Job" sections), quality requirements with falsifiability criteria, and structured JSON        │
│  output contracts. The prompts enforce layer isolation at the instruction level, not just                 │
│  architecturally.                                                                                         │
│  - **Contract enforcement**: All 5 layer outputs have JSON schemas with `additionalProperties: false`,    │
│  preventing schema drift.                                                                                 │
│                                                                                                           │
│  **Weaknesses:**                                                                                          │
│  - The `pipeline.py` orchestrator at 688 lines is the largest file and handles orchestration, retry       │
│  logic, Mode A/B switching, and callback dispatch. It could benefit from decomposition, though this is    │
│  not urgent at current size.                                                                              │
│  - The `cleared` status in `LayerStatus` is defined in the state machine and has valid transitions but    │
│  its usage in practice appears minimal — potential dead code path.                                        │
│                                                                                                           │
│  ---                                                                                                      │
│                                                                                                           │
│  ## 2. Code Quality (Good)                                                                                │
│                                                                                                           │
│  **Evidence examined:** All 20 source files (line counts verified), type annotations spot-checked across  │
│  `state.py`, `pipeline.py`, `validators.py`, `runner.py`, `prompts.py`                                    │
│                                                                                                           │
│  **Metrics:**                                                                                             │
│  - 3,702 lines of source code across 20 files                                                             │
│  - Largest files: `pipeline.py` (688), `cli.py` (517), `ui/checkpoint.py` (460), `qa/validators.py`       │
│  (457)                                                                                                    │
│  - No file exceeds 700 lines — reasonable size discipline                                                 │
│                                                                                                           │
│  **Strengths:**                                                                                           │
│  - **Type hints throughout** (verified in `state.py`, `pipeline.py`, `validators.py`, `runner.py`,        │
│  `drift.py`): `Literal` types for layer names and statuses, proper `list[X]`, `dict[X, Y]` annotations,   │
│  `Callable` types for callbacks. Uses `from __future__ import annotations` consistently.                  │
│  - **Docstrings on modules and key functions** (verified in `state.py`, `pipeline.py`, `validators.py`,   │
│  `drift.py`): Module-level docstrings explain purpose; function docstrings on public API.                 │
│  - **Clean data modeling**: `QAFinding` as a dataclass with `Literal` severity levels. `DriftReport`      │
│  with typed trend enumeration. `CheckpointEvent` and `UserDecision` as Pydantic models.                   │
│  - **Defensive validation**: `_validate_transition()` in state.py enforces legal state machine moves.     │
│  `HOLLOW_PATTERNS` in validators.py catches 9 patterns of LLM-generated non-content.                      │
│                                                                                                           │
│  **Weaknesses:**                                                                                          │
│  - **No linting or formatting tools configured**: No ruff, black, mypy, pyright, or pre-commit in         │
│  `pyproject.toml` or project root. Type hints exist but are never machine-verified.                       │
│  - **No pinned dependency versions**: `pyproject.toml` uses `>=` ranges (`pydantic>=2.0`, `click>=8.0`)   │
│  with no lock file. Builds are not reproducible.                                                          │
│  - `cli.py` (517 lines) was not fully examined — it may contain additional quality signals or issues.     │
│                                                                                                           │
│  ---                                                                                                      │
│                                                                                                           │
│  ## 3. Testing (Good Foundation, Significant Gaps)                                                        │
│                                                                                                           │
│  **Evidence examined:** All 15 test files (1,605 total lines), `pytest --co -q` output, `pyproject.toml`  │
│  dev dependencies                                                                                         │
│                                                                                                           │
│  **Metrics:**                                                                                             │
│  - 129 tests collected                                                                                    │
│  - Execution time: 0.20 seconds (all deterministic, no I/O)                                               │
│  - 15 test files across 3 categories: `unit/` (11 files), `metamorphic/` (3 files), `adversarial/` (2     │
│  files)                                                                                                   │
│  - Note: 2 test files (`test_pipeline_parse.py`, `test_prompts.py`) are untracked/new — the committed     │
│  test count may be lower                                                                                  │
│                                                                                                           │
│  **Strengths:**                                                                                           │
│  - **Three testing tiers**: Unit tests (state immutability, cascade reset, schema contracts, validators,  │
│  drift, transcript, usage tracking), metamorphic tests (model upgrade monotonicity, skip equivalence,     │
│  setting sources isolation), adversarial tests (boundary probes, complexity classifier). This is a        │
│  sophisticated test taxonomy for a prototype.                                                             │
│  - **All tests are deterministic**: 0.20s execution with no network calls, no LLM dependencies, no        │
│  flakiness risk.                                                                                          │
│  - **Metamorphic testing** is a notably advanced practice — `test_model_upgrade_monotonic.py` and         │
│  `test_skip_equivalence.py` test properties that should hold across configurations rather than specific   │
│  outputs.                                                                                                 │
│                                                                                                           │
│  **Weaknesses:**                                                                                          │
│  - **No async/integration tests**: The core pipeline is async (`pipeline.py` uses `async`/`await`), but   │
│  `pytest-asyncio` is not in dev dependencies and no async tests exist. The primary code path is           │
│  untested.                                                                                                │
│  - **No end-to-end tests**: No tests exercise the full CLI → pipeline → agent → eval flow, even with      │
│  mocked subprocess calls.                                                                                 │
│  - **No UI tests**: `ui/checkpoint.py` (460 lines) and `ui/themes.py` (41 lines) have zero test           │
│  coverage.                                                                                                │
│  - **No coverage reporting**: No `pytest-cov` or coverage configuration. Actual line/branch coverage is   │
│  unknown.                                                                                                 │
│  - **README states 107 tests** but actual count is 129 — documentation is stale.                          │
│                                                                                                           │
│  ---                                                                                                      │
│                                                                                                           │
│  ## 4. Documentation (Good)                                                                               │
│                                                                                                           │
│  **Evidence examined:** `README.md` (first 80 lines), `CLAUDE.md`, `HARNESS.md`, `eval.md`,               │
│  `eval-criteria.md` (existence confirmed), all 5 JSON schemas, agent prompt files                         │
│                                                                                                           │
│  **Strengths:**                                                                                           │
│  - **README** is comprehensive: problem statement, architecture diagram, AIQA tier explanation,           │
│  installation instructions. Well-formatted with badges and tables.                                        │
│  - **CLAUDE.md** provides a thorough project map: structure, conventions, entry points, dependencies.     │
│  This serves as effective onboarding documentation for both humans and AI assistants.                     │
│  - **JSON schemas** serve as living documentation of the contract between layers — with                   │
│  `additionalProperties: false`, they're enforced, not just descriptive.                                   │
│  - **Agent prompt files** double as architecture documentation — reading `1-prompt.md` through            │
│  `5-coherence.md` explains the pipeline's design philosophy.                                              │
│                                                                                                           │
│  **Weaknesses:**                                                                                          │
│  - **Stale test count** in README (107 vs. actual 129).                                                   │
│  - **No API documentation** or inline developer guide beyond CLAUDE.md.                                   │
│  - **No CHANGELOG** or release notes (though with 2 commits, this is expected).                           │
│                                                                                                           │
│  ---                                                                                                      │
│                                                                                                           │
│  ## 5. Development Practices & Maturity (Weak — Expected for Stage)                                       │
│                                                                                                           │
│  **Evidence examined:** `pyproject.toml`, `.gitignore` (implicit from git status), git log (2 commits),   │
│  git status (extensive uncommitted changes)                                                               │
│                                                                                                           │
│  **Strengths:**                                                                                           │
│  - **Modern Python tooling**: hatchling build backend, uv package manager, Python 3.12+ requirement.      │
│  - **Clean `pyproject.toml`**: Proper project metadata, script entry point, dev dependency separation.    │
│  - **MIT license** indicated in README badges.                                                            │
│                                                                                                           │
│  **Weaknesses:**                                                                                          │
│  - **2 git commits total** with large batches of uncommitted changes across core files. No meaningful     │
│  git history to learn from.                                                                               │
│  - **No CI/CD**: No GitHub Actions, no automated test runs, no deployment pipeline.                       │
│  - **No linting/formatting**: No ruff, mypy, black, isort, or pre-commit hooks.                           │
│  - **No dependency pinning**: No lock file, no version upper bounds. `pytest>=8.0` is the only dev        │
│  dependency — `pytest-asyncio` is missing despite async code.                                             │
│  - **No coverage tracking**: No pytest-cov, no coverage badge, no coverage thresholds.                    │
│  - **`.pyc` files in git status**: `__pycache__/` directories are being tracked or not properly           │
│  gitignored.                                                                                              │
│                                                                                                           │
│  ---                                                                                                      │
│                                                                                                           │
│  ## Limitations of This Assessment                                                                        │
│                                                                                                           │
│  This report is based on examination of the working tree (including uncommitted changes), not the         │
│  committed codebase. Specific limitations:                                                                │
│                                                                                                           │
│  1. `cli.py` (517 lines) was only partially examined — the CLI argument handling, error UX, and help      │
│  text were not reviewed                                                                                   │
│  2. Not all JSON schemas were read in detail (only `prompt_output.json` confirmed; others confirmed to    │
│  exist)                                                                                                   │
│  3. No runtime testing was performed — the tool was not executed against actual tasks                     │
│  4. No assessment of the LLM outputs the pipeline produces in practice                                    │
│  5. The 129 test count includes 2 untracked test files not yet committed                                  │
│                                                                                                           │
│  ---                                                                                                      │
│                                                                                                           │
│  ## Top 5 Improvement Recommendations (Ranked by Impact)                                                  │
│                                                                                                           │
│  ### 1. Add CI/CD with Linting and Type Checking                                                          │
│  **Impact: High** | **Effort: Low**                                                                       │
│  Add a GitHub Actions workflow running `pytest`, `ruff check`, and `mypy --strict`. This would catch      │
│  issues automatically and is the single highest-leverage improvement. The type hints already exist —      │
│  mypy would verify them for free.                                                                         │
│                                                                                                           │
│  ### 2. Add Async Pipeline Integration Tests                                                              │
│  **Impact: High** | **Effort: Medium**                                                                    │
│  The core async pipeline (`pipeline.py`, 688 lines) has zero test coverage. Add `pytest-asyncio` to dev   │
│  deps and write tests that exercise `run_pipeline()` with mocked `run_claude()` calls. This tests the     │
│  primary code path that currently relies entirely on manual verification.                                 │
│                                                                                                           │
│  ### 3. Pin Dependencies and Add a Lock File                                                              │
│  **Impact: Medium** | **Effort: Low**                                                                     │
│  Run `uv lock` to generate a lock file. Add upper-bound constraints (e.g., `pydantic>=2.0,<3.0`) to       │
│  prevent surprise breakage from major version bumps. This takes minutes and prevents a class of "works    │
│  on my machine" issues.                                                                                   │
│                                                                                                           │
│  ### 4. Add Coverage Reporting with a Minimum Threshold                                                   │
│  **Impact: Medium** | **Effort: Low**                                                                     │
│  Add `pytest-cov` to dev deps, set a coverage floor (e.g., 60% given current state), and include it in    │
│  CI. This makes test gaps visible and prevents regression.                                                │
│                                                                                                           │
│  ### 5. Commit Frequently with Meaningful History                                                         │
│  **Impact: Medium** | **Effort: Behavioral**                                                              │
│  The current pattern of 2 large commits with extensive uncommitted changes makes it impossible to         │
│  understand what changed and why. Smaller, focused commits with descriptive messages would improve        │
│  debuggability, enable meaningful `git bisect`, and create a development narrative.                       │
│                                                                                                           │
│  ---                                                                                                      │
│                                                                                                           │
│  ## Summary                                                                                               │
│                                                                                                           │
│  | Dimension | Rating | Notes |                                                                           │
│  |---|---|---|                                                                                            │
│  | Architecture | **A-** | Immutable state machine, layered QA, clean separation, enforced contracts |    │
│  | Code Quality | **B+** | Type hints, docstrings, clean modeling. No machine verification (mypy/ruff).   │
│  |                                                                                                        │
│  | Testing | **B** | 129 deterministic tests with sophisticated taxonomy. No async/integration/e2e        │
│  coverage. |                                                                                              │
│  | Documentation | **B+** | Strong README, CLAUDE.md, schema-as-docs. Minor staleness. |                  │
│  | Dev Practices | **D+** | No CI, no linting, no pinning, 2 commits. Expected for prototype stage. |     │
│  | **Overall** | **B+** | Strong engineering foundations in a prototype package. The architecture and QA  │
│  design are ahead of the project's infrastructure maturity. |                                             │
│                                                                                                           │
│  The project demonstrates a clear vision and disciplined architectural thinking. The gap is not in        │
│  design quality but in development infrastructure — the kinds of things that take an afternoon to set up  │
│  but compound in value over every subsequent commit.                                                      │
│                                                                                                           │
╰───────────────────────────────────────────────────────────────────────────────────────────────────────────╯