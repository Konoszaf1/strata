<div align="center">

# Strata: Layered Cognitive Pipeline for Claude Code

### What if your AI thought in layers — prompt, context, intent, judgment, coherence — with quality gates between each?

[![Python 3.12+](https://img.shields.io/badge/python-3.12+-3776AB?style=for-the-badge&logo=python&logoColor=white)](https://www.python.org/downloads/)
[![Claude Code](https://img.shields.io/badge/Claude_Code-CLI-D97757?style=for-the-badge&logo=anthropic&logoColor=white)](https://docs.anthropic.com/en/docs/claude-code)
[![Pytest](https://img.shields.io/badge/tested_with-pytest-0A9EDC?style=for-the-badge&logo=pytest&logoColor=white)](https://pytest.org/)
[![Pydantic v2](https://img.shields.io/badge/models-Pydantic_v2-E92063?style=for-the-badge&logo=pydantic&logoColor=white)](https://docs.pydantic.dev/)
[![uv](https://img.shields.io/badge/package-uv-DE5FE9?style=for-the-badge)](https://docs.astral.sh/uv/)
[![License: MIT](https://img.shields.io/badge/License-MIT-green?style=for-the-badge)](LICENSE)

</div>

---

## The Problem

Ask an AI to "refactor the auth module" and it produces code. Sometimes great code. Sometimes code that ignores your project conventions, breaks an unstated constraint, or optimizes for the wrong thing. The output *looks* correct — it compiles, it follows the prompt — but it misses what you actually wanted because nobody told it what to value, what to doubt, or how to stay consistent.

Most AI tools operate on a single axis: prompt in, output out. When the output is wrong, the only lever is "write a better prompt." But often the problem isn't the prompt. It's missing context, unspoken intent, absent judgment about edge cases, or inconsistency with prior work. These are categorically different problems that require different interventions.

<div align="center">

| What went wrong | Actual cause | Typical (wrong) fix |
|---|---|---|
| Code ignores existing patterns | Missing **context** | Rewrite the prompt |
| Optimizes for speed over readability | Missing **intent** | Add more instructions |
| Doesn't flag a risky assumption | Missing **judgment** | Hope it works |
| Different style than last session | Missing **coherence** | Manual cleanup |

</div>

---

## What Strata Does

Strata orchestrates Claude Code CLI through **five sequential cognitive layers**, each running as an isolated subprocess with its own model, tools, and constraints. An eval agent runs between each layer, and a human (or auto) gate decides whether to proceed, retry, or cascade back.

Instead of one prompt producing one output, a task flows through structured reasoning:

```
User Prompt
    |
    v
[ 1. Prompt ]  →  Parse task, classify complexity, surface ambiguities
    |
[ 2. Context ] →  Explore codebase, gather files, compress findings
    |
[ 3. Intent ]  →  Define success criteria, tradeoffs, decision boundaries
    |
[ 4. Judgment ] → Stress-test assumptions, flag risks, set degradation protocol
    |
[ 5. Coherence ] → Produce final output aligned with project identity
    |
    v
Final Output
```

Each layer is evaluated before the next one runs. If the eval or the human finds a problem, the pipeline can:

- **Mode A (Cascade Reset):** Go back to a lower layer, wiping everything above it
- **Mode B (Reprompt):** Retry the current layer with feedback, preserving session history

This maps to the [AI-Human Engineering Stack](https://github.com/hjasanchez/agentic-engineering/blob/main/The%20AI-Human%20Engineering%20Stack.pdf) paper by Mill & Sanchez (March 2026), which argues these five layers represent categorically distinct cognitive concerns that cannot be collapsed into each other.

---

## AIQA: Three-Tier Quality Assurance

Every layer output passes through a quality assurance pipeline before reaching the eval agent. This catches structural problems fast and gives the eval agent richer signal.

| Tier | What it does | How it works |
|---|---|---|
| **Tier 1: Structural** | Schema completeness, hollowness detection, referential integrity, boundary violations | Deterministic Python checks — no LLM calls. Runs in < 50ms. |
| **Tier 2: Semantic** | Accuracy, completeness, consistency, groundedness, proportionality | Eval agent scores five quality dimensions per layer. |
| **Tier 3: Drift** | Cross-run quality trending, recurring failure detection, pattern conflicts | Reads previous transcripts to detect quality degradation over time. |

### What Each Tier Catches

**Tier 1 — Structural Validators** (`src/app/qa/validators.py`)

| Check | What it catches | Example |
|---|---|---|
| Hollowness | Fields that satisfy the schema but contain no substance | `"constraints": "N/A"`, or all array items are single words |
| Referential integrity | File paths cited in Context that don't exist on disk | `"path": "src/auth.py", "verified": true` but file is missing |
| Boundary violation | Concepts that leak across layer boundaries | Judgment agent producing implementation code |
| Range constraints | Complexity vs. recommended layers mismatch, uniform risk severity | All 5 risks rated "low" on a critical task |
| Completeness | Missing required fields for the task's complexity level | No tradeoffs on a medium-complexity task, empty unknowns |

**Tier 2 — Semantic Quality** (eval agent, per-layer)

The eval agent scores every layer across five dimensions and returns structured quality scores alongside its verdict. Tier 1 findings are injected into the eval prompt — failures should cause a `fail` verdict unless the eval can explain why the structural check doesn't apply.

**Tier 3 — Drift Detection** (`src/app/qa/drift.py`)

Analyzes the last 10 transcripts to detect:
- Quality score trends (improving / stable / declining)
- Layers that fail repeatedly across runs
- Coherence pattern conflicts (elevated drift_risk, recurring principle violations)

The drift report is injected into the Coherence layer's prompt so it can account for longitudinal patterns.

---

## Layer Architecture

Each layer answers a different question and has strict boundaries about what it can and cannot do:

| Layer | Question | Model | Tools | Key output fields |
|---|---|---|---|---|
| **Prompt** | What to do? | Haiku | Read, Glob | task_description, complexity, ambiguities, assumptions |
| **Context** | What to know? | Sonnet | Read, Glob, Grep, Bash | sources (verified), gathered_info, distilled_context, gaps |
| **Intent** | What to want? | Opus | Read, Glob | tradeoffs, decision_boundaries, priority_order (with `because`) |
| **Judgment** | What to doubt? | Opus | Read, Glob, Grep | confidence_boundaries, degradation_protocol, risks (with `detectable`) |
| **Coherence** | What to become? | Sonnet | Read, Glob, Skill | final_output, consistency_check, judgment_responses, drift_risk |

### Context Isolation

Each layer runs as a separate `claude -p` subprocess. There is no shared memory, no context bleed, no accidental information leakage. This is the strongest form of the paper's "Isolate" operation — enforced at the infrastructure level.

### Judgment Force-Gate

When the Judgment layer outputs `go_no_go: "reconsider"`, the pipeline forces human review regardless of the `eval_gate` setting. This implements the paper's principle that judgment should be "conservative by default" and can override system autonomy.

---

## Pipeline State Machine

All state is immutable. Every transition produces a new frozen Pydantic model with the previous state preserved in a capped history chain. Invalid transitions (e.g., approving a layer that isn't running) raise `ValueError`.

```
None ──> running ──> approved ──> running (re-run after cascade)
                 ──> rejected ──> running (Mode B retry)
                 ──> skipped  ──> running (if revisited)
```

Transition validation prevents impossible state sequences. History is capped at 20 entries to bound memory.

---

## Quickstart

### Prerequisites

- Python 3.12+
- [uv](https://docs.astral.sh/uv/)
- [Claude Code CLI](https://docs.anthropic.com/en/docs/claude-code) (`npm install -g @anthropic-ai/claude-code`)

### Install

```bash
git clone https://github.com/Konoszaf1/Strata.git && cd Strata
uv pip install -e '.[dev]'
```

### Run

```bash
# Full pipeline with human gating (default)
strata "refactor the auth module to use async/await"

# Fast autonomous run
strata --skip=always --gate=auto "rename usr to user everywhere"

# See what would happen without executing
strata --dry-run "add pagination to the API"

# View rate limit budget
strata --budget

# List previous run transcripts
strata --transcript
```

### As a Claude Code Skill

Strata ships as a Claude Code skill. From any conversation:

```
/stack-pipeline "refactor the auth module to use async/await"
```

### Tests

```bash
# Unit tests (107 tests, ~0.3s)
uv run pytest tests/unit/ -v

# Full suite (unit + adversarial + metamorphic)
uv run pytest tests/ -v

# Specific test category
uv run pytest tests/adversarial/ -v
uv run pytest tests/metamorphic/ -v
```

---

## Configuration

Config merges in order: **harness defaults** -> **user** `~/.stack/config.yaml` -> **project** `.stack/config.yaml` -> **CLI flags**.

```yaml
pipeline:
  skip_policy: recommended    # never | next | recommended | always
  eval_gate: human            # human | auto
  max_retries_per_layer: 3
  plan: max_5x                # max_5x | max_20x

  layers:
    prompt:
      model: haiku
      max_turns: 3
      allowed_tools: [Read, Glob]
    context:
      model: sonnet
      max_turns: 10
      allowed_tools: [Read, Glob, Grep, Bash]
    intent:
      model: opus
      max_turns: 5
      allowed_tools: [Read, Glob]
    judgment:
      model: opus
      max_turns: 5
      allowed_tools: [Read, Glob, Grep]
    coherence:
      model: sonnet
      max_turns: 3
      allowed_tools: [Read, Glob, Skill]
      setting_sources: [user, project]
```

Custom harness directories let you swap the entire prompt set and config:

```bash
strata --harness ./my-custom-harness "your prompt here"
```

---

## Project Structure

```
src/app/                          # Main application
  cli.py                          #   Click CLI entry point
  pipeline.py                     #   Core orchestrator — layer->eval->checkpoint loop
  state.py                        #   Immutable state models, transition validation
  config.py                       #   Layered config merge (harness->user->project->CLI)
  bootstrap.py                    #   Auto-generate CLAUDE.md for bare projects

  agents/                         # Claude CLI subprocess management
    runner.py                     #   claude -p wrapper, rate limit retry, JSON parse
    prompts.py                    #   Prompt construction for layers and eval
    validation.py                 #   Session resume validation (--resume integrity)

  qa/                             # AIQA — three-tier quality assurance
    validators.py                 #   Tier 1: structural checks (hollowness, referential integrity)
    drift.py                      #   Tier 3: cross-run quality drift detection
    boundary_check.py             #   Layer boundary violation detection
    transcript.py                 #   Full run transcript writer (crash-resilient)
    usage_tracker.py              #   Token usage tracking, rate limit estimation

  harness/                        # Shipped agent prompts and schemas
    agents/                       #   1-prompt.md through 5-coherence.md, eval.md
    schemas/                      #   JSON Schema contracts for each layer's output
    docs/                         #   Harness documentation
    config.yaml                   #   Default pipeline configuration

  ui/                             # Rich terminal UI
    checkpoint.py                 #   Human gating prompts, layer status rendering
    themes.py                     #   Console theme

tests/
  unit/                           # 84 tests — state, config, schema, validators, drift, transcript
  adversarial/                    # 15 tests — boundary probes, complexity classifier
  metamorphic/                    # 8 tests — model upgrade, skip equivalence, setting isolation

.claude/skills/                   # Claude Code skill wrapper
  stack-pipeline/SKILL.md         #   /stack-pipeline slash command
```

---

## Test Suite

107 tests across three categories, all deterministic (no API calls):

| Category | Tests | What it validates |
|---|:---:|---|
| **Unit** | 84 | State immutability, transition validation, config merge, boundary checks, AIQA validators, drift detection, transcript writing, session validation, usage tracking |
| **Adversarial** | 15 | Boundary probe attacks (smuggling concepts across layers), complexity classifier edge cases |
| **Metamorphic** | 8 | Model upgrade monotonicity, skip equivalence, setting sources isolation |

---

## Tech Stack

| | Technology |
|---|---|
| Runtime | Python 3.12+ with uv |
| LLM | Claude Code CLI (`claude -p` subprocess) |
| Models | Haiku (prompt), Sonnet (context, coherence), Opus (intent, judgment) |
| State | Frozen Pydantic v2 models with transition validation |
| CLI | Click with Rich terminal UI |
| Config | YAML with four-level merge cascade |
| QA | Three-tier AIQA (structural validators, semantic eval, drift detection) |
| Testing | pytest (unit + adversarial + metamorphic) |
| Build | Hatchling backend, uv for dependency management |

---

## Theoretical Foundation

Strata implements the [AI-Human Engineering Stack](https://github.com/hjasanchez/agentic-engineering/blob/main/The%20AI-Human%20Engineering%20Stack.pdf) (Mill & Sanchez, March 2026), which identifies five categorically distinct layers of cognitive concern in AI-human collaboration, plus two meta-functions (Evaluation Engineering and Harness Engineering).

The paper's key insight: most AI frustration comes from trying to fix context problems with better prompts, or judgment problems with more context. Each layer introduces a question that cannot be reduced to any other layer. The stack gives you a vocabulary for diagnosing *which layer* is failing, not just that the output is wrong.

---

## Author

**Konstantinos Zafeiris**
</div>
