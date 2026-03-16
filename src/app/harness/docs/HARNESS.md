# AI-Human Engineering Stack — Harness Documentation

## What Is the Harness?

The harness is the infrastructure around the five cognitive layers. It includes:

- **Agent system prompts** (`agents/*.md`) — cognitive identity for each layer
- **Pipeline configuration** (`config.yaml`) — model routing, skip policies, gating
- **JSON schemas** (`schemas/*.json`) — output contracts for each layer
- **Evaluation criteria** (`docs/eval-criteria.md`) — per-layer quality checklist

## The Five Layers

| # | Layer | Question | Model (default) |
|---|-------|----------|-----------------|
| 1 | Prompt | "What to do" | haiku |
| 2 | Context | "What to know while doing" | sonnet |
| 3 | Intent | "What to want while doing" | opus |
| 4 | Judgment | "What to doubt while doing" | opus |
| 5 | Coherence | "What to become while doing" | sonnet |

Each layer runs as an isolated `claude -p` subprocess with its own session,
system prompt, and tool restrictions.

## Layer Isolation

- Only **Coherence** loads the project's `CLAUDE.md` and skills (`--setting-sources user,project`)
- All other layers operate in cognitive isolation
- Each layer has **forbidden concepts** that belong to other layers
- The eval agent checks for boundary violations

## Session Persistence

- Each layer gets a unique session ID from Claude CLI
- **Mode B (reprompt current):** Uses `--resume` to continue the existing session
- **Mode A (cascade reset):** Discards sessions above the target, creates fresh ones
- Eval sessions are always fresh (no resume)

## Configuration Override

Config is merged in this order (highest priority last):

1. `harness/config.yaml` — shipped defaults
2. `~/.stack/config.yaml` — user global
3. `.stack/config.yaml` — project specific
4. CLI flags (`--skip`, `--gate`, `--plan`)

## Customizing

To create a custom harness:
1. Copy the `harness/` directory
2. Modify agent prompts, config, or schemas
3. Run with `--harness /path/to/your/harness`
