"""Boundary check — detect when a layer's output contains concepts outside its scope.

Each layer has forbidden concepts that belong to other layers. If a forbidden term
appears in the output, it's flagged for the eval agent. If the term also appears
in the original user prompt, it's logged as user-originated, not a violation.
"""

from __future__ import annotations

import json
import re

from app.state import LayerName

# Concepts that should NOT appear in a given layer's output.
# Coherence has no forbidden concepts — it integrates everything.
FORBIDDEN_CONCEPTS: dict[str, list[str]] = {
    "prompt": [
        "risk", "vulnerability", "alternative approach", "project identity",
        "CLAUDE.md", "project convention",
    ],
    "context": [
        "goal", "success criteria", "risk assessment", "should want",
        "CLAUDE.md", "project convention",
    ],
    "intent": [
        "risk", "doubt", "might fail", "alternative approach",
        "CLAUDE.md", "project convention",
    ],
    "judgment": [
        "final implementation", "here is the code", "the solution is",
        "CLAUDE.md", "project convention",
    ],
}


def check_boundaries(
    layer: LayerName,
    output: dict,
    original_prompt: str,
) -> list[str]:
    """Check layer output for forbidden concepts.

    Returns a list of violation descriptions. Empty list means clean.
    Terms that also appear in the original prompt are noted as user-originated.
    """
    forbidden = FORBIDDEN_CONCEPTS.get(layer)
    if not forbidden:
        return []

    output_text = json.dumps(output, default=str).lower()
    prompt_lower = original_prompt.lower()
    violations: list[str] = []

    for term in forbidden:
        pattern = re.compile(re.escape(term.lower()))
        if pattern.search(output_text):
            if pattern.search(prompt_lower):
                violations.append(
                    f"[user_originated] '{term}' found in {layer} output "
                    f"(also present in user prompt)"
                )
            else:
                violations.append(
                    f"[boundary_violation] '{term}' found in {layer} output — "
                    f"this concept belongs to a different layer"
                )

    return violations
