"""Project bootstrap — auto-generate context files for bare projects.

When a target project has no CLAUDE.md, README, or other documentation,
the Context layer struggles. This module detects that situation and
runs a quick investigation to generate minimal project context files.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

from app.agents.runner import run_claude

logger = logging.getLogger(__name__)

# Files that signal the project already has useful context
CONTEXT_INDICATORS = [
    "CLAUDE.md",
    "README.md",
    "README.rst",
    "README.txt",
    "README",
    "docs/",
    ".stack/context.md",
]


def needs_bootstrap(project_dir: str) -> bool:
    """Return True if the project lacks basic context files."""
    root = Path(project_dir)
    for indicator in CONTEXT_INDICATORS:
        target = root / indicator
        if target.exists():
            return False
    return True


BOOTSTRAP_PROMPT = """\
You are investigating a project directory to create a CLAUDE.md file for it.
Explore the project structure, read key files (package.json, pyproject.toml,
Cargo.toml, go.mod, Makefile, setup.py, etc.), and understand what this project is.

Respond with ONLY a JSON object:
{
  "project_name": "name of the project",
  "description": "1-2 sentence description of what this project does",
  "language": "primary language(s)",
  "build_system": "how to build/install (command or null)",
  "test_command": "how to run tests (command or null)",
  "entry_points": ["main entry point files"],
  "key_directories": {"dir_name": "purpose"},
  "conventions": ["any notable patterns or conventions observed"],
  "dependencies_summary": "brief summary of key dependencies"
}
"""

CLAUDE_MD_TEMPLATE = """\
# {project_name}

{description}

## Language & Build

- **Language**: {language}
- **Build**: {build_system}
- **Tests**: {test_command}

## Structure

{structure}

## Entry Points

{entry_points}

## Conventions

{conventions}

## Dependencies

{dependencies_summary}
"""


async def run_bootstrap(
    project_dir: str,
    model: str = "sonnet",
) -> tuple[Path | None, dict]:
    """Investigate the project and generate a CLAUDE.md.

    Returns (path_to_created_file, investigation_result).
    Returns (None, {}) if bootstrap is not needed.
    """
    if not needs_bootstrap(project_dir):
        return None, {}

    harness_dir = Path(__file__).resolve().parent / "harness"
    # We don't use an agent file for bootstrap — just a direct prompt
    # But run_claude requires append_system_prompt_file, so we use a temp approach
    # Instead, we'll use a minimal system prompt inline

    raw_result = await run_claude(
        prompt=BOOTSTRAP_PROMPT,
        append_system_prompt_file=harness_dir / "agents" / "2-context.md",
        model=model,
        max_turns=5,
        allowed_tools=["Read", "Glob", "Grep", "Bash"],
        project_dir=project_dir,
    )

    # Parse the result
    text = raw_result.get("result", "")
    cleaned = text.strip()
    if cleaned.startswith("```"):
        lines = cleaned.split("\n")
        if lines[-1].strip() == "```":
            lines = lines[1:-1]
        else:
            lines = lines[1:]
        cleaned = "\n".join(lines).strip()

    try:
        info = json.loads(cleaned)
    except json.JSONDecodeError:
        logger.warning("Bootstrap investigation did not return valid JSON")
        return None, {"_raw": text, "_parse_error": "Invalid JSON from bootstrap"}

    # Generate CLAUDE.md
    structure = (
        "\n".join(
            f"- **{d}**: {purpose}"
            for d, purpose in info.get("key_directories", {}).items()
        )
        or "- *(not yet documented)*"
    )

    entry_points = (
        "\n".join(f"- `{ep}`" for ep in info.get("entry_points", []))
        or "- *(not yet documented)*"
    )

    conventions = (
        "\n".join(f"- {c}" for c in info.get("conventions", []))
        or "- *(none detected)*"
    )

    claude_md = CLAUDE_MD_TEMPLATE.format(
        project_name=info.get("project_name", "Unknown Project"),
        description=info.get("description", "No description available."),
        language=info.get("language", "unknown"),
        build_system=info.get("build_system") or "not detected",
        test_command=info.get("test_command") or "not detected",
        structure=structure,
        entry_points=entry_points,
        conventions=conventions,
        dependencies_summary=info.get("dependencies_summary", "not analyzed"),
    )

    output_path = Path(project_dir) / "CLAUDE.md"
    output_path.write_text(claude_md, encoding="utf-8")
    logger.info("Bootstrap created %s", output_path)

    return output_path, info
