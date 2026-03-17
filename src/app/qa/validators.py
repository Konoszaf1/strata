"""AIQA Tier 1 — Structural validators that run before eval.

Fast, deterministic checks with no LLM calls. They catch:
- Schema completeness (missing required fields, wrong types)
- Hollowness (fields present but empty of substance)
- Boundary violations (concept leak across layers)
- Referential integrity (cited files actually exist)
- Range constraints (field values within expected bounds)
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

import jsonschema

from app.qa.boundary_check import check_boundaries
from app.state import LayerName, PipelineState


@dataclass
class QAFinding:
    """A single AIQA finding from structural validation."""

    tier: Literal["structural", "semantic", "drift"]
    severity: Literal["info", "warning", "failure"]
    check: str
    layer: LayerName
    detail: str
    auto_fixable: bool = False


# ---------------------------------------------------------------------------
# Hollow-content patterns — signs the LLM produced form without substance
# ---------------------------------------------------------------------------

HOLLOW_PATTERNS: list[re.Pattern] = [
    re.compile(r"^N/?A$", re.IGNORECASE),
    re.compile(r"^None\.?$", re.IGNORECASE),
    re.compile(r"^TBD\.?$", re.IGNORECASE),
    re.compile(r"^TODO\.?$", re.IGNORECASE),
    re.compile(r"^No .{0,30} identified\.?$", re.IGNORECASE),
    re.compile(r"^Standard .{0,30} apply\.?$", re.IGNORECASE),
    re.compile(r"^Not applicable\.?$", re.IGNORECASE),
    re.compile(r"^See above\.?$", re.IGNORECASE),
    re.compile(r"^As (described|noted|mentioned)\.?", re.IGNORECASE),
]


def _check_schema_compliance(layer: LayerName, output: dict) -> list[QAFinding]:
    """Validate output against the layer's JSON Schema file."""
    schema_path = (
        Path(__file__).resolve().parent.parent
        / "harness"
        / "schemas"
        / f"{layer}_output.json"
    )

    if not schema_path.exists():
        return [
            QAFinding(
                tier="structural",
                severity="info",
                check="schema_compliance",
                layer=layer,
                detail=f"No schema file found at {schema_path.name} — skipping schema validation",
            )
        ]

    try:
        with open(schema_path) as f:
            schema = json.load(f)
    except Exception as exc:
        return [
            QAFinding(
                tier="structural",
                severity="warning",
                check="schema_compliance",
                layer=layer,
                detail=f"Failed to load schema: {str(exc)[:200]}",
            )
        ]

    try:
        jsonschema.validate(instance=output, schema=schema)
    except jsonschema.ValidationError as exc:
        json_path = "$" + "".join(
            f".{p}" if isinstance(p, str) else f"[{p}]"
            for p in exc.absolute_path
        )
        detail = f"Schema validation failed at {json_path}: {exc.message}"
        return [
            QAFinding(
                tier="structural",
                severity="failure",
                check="schema_compliance",
                layer=layer,
                detail=detail[:200],
            )
        ]
    except jsonschema.SchemaError as exc:
        return [
            QAFinding(
                tier="structural",
                severity="warning",
                check="schema_compliance",
                layer=layer,
                detail=f"Malformed schema: {str(exc.message)[:200]}",
            )
        ]

    return []


def validate_layer_output(
    layer: LayerName,
    output: dict,
    state: PipelineState,
    project_dir: str,
) -> list[QAFinding]:
    """Run all Tier 1 checks for a layer output.

    Returns findings sorted by severity (failure first).
    """
    findings: list[QAFinding] = []
    findings += _check_schema_compliance(layer, output)
    findings += _check_hollowness(layer, output)
    findings += _check_boundary_violations(layer, output, state.original_prompt)
    findings += _check_referential_integrity(layer, output, project_dir)
    findings += _check_range_constraints(layer, output, state)
    findings += _check_layer_specific(layer, output, state)

    severity_order = {"failure": 0, "warning": 1, "info": 2}
    findings.sort(key=lambda f: severity_order.get(f.severity, 9))
    return findings


# ---------------------------------------------------------------------------
# Check: Hollowness
# ---------------------------------------------------------------------------


def _check_hollowness(layer: LayerName, output: dict) -> list[QAFinding]:
    """Detect outputs that satisfy the schema but contain no substance."""
    findings: list[QAFinding] = []

    for key, val in output.items():
        if key.startswith("_"):
            continue

        # String fields: check for hollow patterns
        if isinstance(val, str):
            stripped = val.strip()
            for pattern in HOLLOW_PATTERNS:
                if pattern.match(stripped):
                    findings.append(QAFinding(
                        tier="structural",
                        severity="warning",
                        check="hollowness",
                        layer=layer,
                        detail=f"'{key}' contains hollow content: '{stripped}'",
                    ))
                    break

        # Array fields: check for all-short-label entries
        if isinstance(val, list) and len(val) > 0:
            if all(isinstance(v, str) and len(v.split()) <= 3 for v in val):
                findings.append(QAFinding(
                    tier="structural",
                    severity="warning",
                    check="hollowness",
                    layer=layer,
                    detail=f"'{key}' contains only short labels — may lack substance",
                ))

    return findings


# ---------------------------------------------------------------------------
# Check: Boundary Violations (wraps existing boundary_check.py)
# ---------------------------------------------------------------------------


def _check_boundary_violations(
    layer: LayerName,
    output: dict,
    original_prompt: str,
) -> list[QAFinding]:
    """Wrap existing boundary check into QAFinding format."""
    violations = check_boundaries(layer, output, original_prompt)
    findings: list[QAFinding] = []

    for v in violations:
        is_user = v.startswith("[user_originated]")
        findings.append(QAFinding(
            tier="structural",
            severity="info" if is_user else "warning",
            check="boundary_violation",
            layer=layer,
            detail=v,
        ))

    return findings


# ---------------------------------------------------------------------------
# Check: Referential Integrity (Context layer only)
# ---------------------------------------------------------------------------


def _check_referential_integrity(
    layer: LayerName,
    output: dict,
    project_dir: str,
) -> list[QAFinding]:
    """Verify that file paths cited in Context output actually exist."""
    if layer != "context":
        return []

    sources = output.get("sources", [])
    if not isinstance(sources, list):
        return []

    findings: list[QAFinding] = []
    project = Path(project_dir)

    for source in sources:
        if not isinstance(source, dict):
            continue
        path = source.get("path", "")
        verified = source.get("verified", True)
        source_type = source.get("type", "")

        if not path or source_type == "git":
            continue

        # Resolve relative paths against project dir
        full_path = project / path if not Path(path).is_absolute() else Path(path)

        if not full_path.exists():
            if verified:
                findings.append(QAFinding(
                    tier="structural",
                    severity="failure",
                    check="referential_integrity",
                    layer=layer,
                    detail=f"Source marked verified but does not exist: {path}",
                ))
            else:
                findings.append(QAFinding(
                    tier="structural",
                    severity="info",
                    check="referential_integrity",
                    layer=layer,
                    detail=f"Unverified source path does not exist: {path}",
                ))

    return findings


# ---------------------------------------------------------------------------
# Check: Range Constraints
# ---------------------------------------------------------------------------


COMPLEXITY_TO_MIN_LAYERS = {
    "trivial": 1,
    "low": 1,
    "medium": 3,
    "high": 4,
    "critical": 5,
}


def _check_range_constraints(
    layer: LayerName,
    output: dict,
    state: PipelineState,
) -> list[QAFinding]:
    """Check that field values fall within expected ranges."""
    findings: list[QAFinding] = []

    if layer == "prompt":
        complexity = output.get("complexity", {})
        level = complexity.get("level", "")
        recommended = complexity.get("recommended_layers", [])
        min_layers = COMPLEXITY_TO_MIN_LAYERS.get(level, 0)

        if recommended and len(recommended) < min_layers:
            findings.append(QAFinding(
                tier="structural",
                severity="warning",
                check="range_constraint",
                layer=layer,
                detail=(
                    f"Complexity '{level}' expects >= {min_layers} recommended layers, "
                    f"but only {len(recommended)} provided"
                ),
            ))

    if layer == "judgment":
        risks = output.get("risks", [])
        if isinstance(risks, list) and len(risks) > 1:
            severities = [r.get("severity") for r in risks if isinstance(r, dict)]
            unique = set(severities)
            if len(unique) == 1 and len(severities) > 2:
                findings.append(QAFinding(
                    tier="structural",
                    severity="warning",
                    check="range_constraint",
                    layer=layer,
                    detail=f"All {len(severities)} risks have identical severity '{severities[0]}' — likely miscalibrated",
                ))

    return findings


# ---------------------------------------------------------------------------
# Check: Layer-Specific Rules
# ---------------------------------------------------------------------------


def _get_task_complexity(state: PipelineState) -> str | None:
    """Extract complexity level from the Prompt layer output."""
    prompt_lr = state.layers.get("prompt")
    if prompt_lr and prompt_lr.output:
        return prompt_lr.output.get("complexity", {}).get("level")
    return None


def _check_layer_specific(
    layer: LayerName,
    output: dict,
    state: PipelineState,
) -> list[QAFinding]:
    """Per-layer structural rules beyond basic schema."""
    findings: list[QAFinding] = []
    complexity = _get_task_complexity(state)

    if layer == "prompt":
        # Medium+ tasks should have ambiguities
        ambiguities = output.get("ambiguities", [])
        if complexity in ("medium", "high", "critical") and not ambiguities:
            findings.append(QAFinding(
                tier="structural",
                severity="warning",
                check="completeness",
                layer=layer,
                detail=f"Complexity is '{complexity}' but no ambiguities identified — unlikely for a complex task",
            ))

    if layer == "context":
        # distilled_context should be shorter than gathered_info
        gathered = output.get("gathered_info", "")
        distilled = output.get("distilled_context", "")
        if gathered and distilled and len(distilled) >= len(gathered):
            findings.append(QAFinding(
                tier="structural",
                severity="warning",
                check="compression",
                layer=layer,
                detail="distilled_context is not shorter than gathered_info — compression did not occur",
            ))

    if layer == "intent":
        # Tradeoffs required for medium+
        tradeoffs = output.get("tradeoffs", [])
        if complexity in ("medium", "high", "critical") and not tradeoffs:
            findings.append(QAFinding(
                tier="structural",
                severity="warning",
                check="completeness",
                layer=layer,
                detail=f"Complexity is '{complexity}' but no tradeoffs identified",
            ))

        # Decision boundaries should exist
        boundaries = output.get("decision_boundaries", [])
        if not boundaries:
            findings.append(QAFinding(
                tier="structural",
                severity="warning",
                check="completeness",
                layer=layer,
                detail="No decision boundaries defined — unclear what the agent can decide autonomously",
            ))

        # Priority order should have 'because'
        priorities = output.get("priority_order", [])
        for p in priorities:
            if isinstance(p, dict) and not p.get("because"):
                findings.append(QAFinding(
                    tier="structural",
                    severity="warning",
                    check="hollowness",
                    layer=layer,
                    detail=f"Priority '{p.get('goal', '?')}' has no 'because' — missing justification",
                ))
                break  # One warning is enough

    if layer == "judgment":
        # confidence_boundaries.unknowns must be non-empty
        cb = output.get("confidence_boundaries", {})
        unknowns = cb.get("unknowns", []) if isinstance(cb, dict) else []
        if not unknowns:
            findings.append(QAFinding(
                tier="structural",
                severity="warning",
                check="completeness",
                layer=layer,
                detail="confidence_boundaries.unknowns is empty — every task has unknowns",
            ))

        # degradation_protocol required for high/critical
        dp = output.get("degradation_protocol")
        if complexity in ("high", "critical") and not dp:
            findings.append(QAFinding(
                tier="structural",
                severity="warning",
                check="completeness",
                layer=layer,
                detail=f"Complexity is '{complexity}' but no degradation_protocol provided",
            ))

    if layer == "coherence":
        # Every Judgment risk should have a response
        judgment_lr = state.layers.get("judgment")
        if judgment_lr and judgment_lr.output:
            judgment_risks = judgment_lr.output.get("risks", [])
            responses = output.get("judgment_responses", [])
            response_risks = {r.get("risk", "") for r in responses if isinstance(r, dict)}

            if len(judgment_risks) > 0 and not responses:
                findings.append(QAFinding(
                    tier="structural",
                    severity="failure",
                    check="completeness",
                    layer=layer,
                    detail=f"Judgment identified {len(judgment_risks)} risks but Coherence has no judgment_responses",
                ))

        # consistency_check should be present and non-hollow
        cc = output.get("consistency_check", {})
        if isinstance(cc, dict):
            if not cc.get("prior_patterns") or not cc.get("style_coherence"):
                findings.append(QAFinding(
                    tier="structural",
                    severity="warning",
                    check="hollowness",
                    layer=layer,
                    detail="consistency_check has empty prior_patterns or style_coherence",
                ))

    return findings
