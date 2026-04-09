"""Prompt construction for layer agents and the eval agent."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.qa.validators import QAFinding

from app.state import LAYER_ORDER, LayerName, PipelineState


def _slim_context_for_downstream(context_output: dict) -> dict:
    """Strip verbose fields from Context output for downstream layers.

    Downstream layers should receive distilled_context (the compressed
    briefing), dependencies, and gaps — NOT the full gathered_info or
    raw source listings. This respects the Context agent's compression
    work and saves tokens.
    """
    return {
        "distilled_context": context_output.get("distilled_context", ""),
        "dependencies": context_output.get("dependencies", []),
        "gaps": context_output.get("gaps", []),
        "relevant_history": context_output.get("relevant_history", ""),
        "source_count": len(context_output.get("sources", [])),
    }


def build_layer_prompt(
    layer: LayerName,
    state: PipelineState,
    user_feedback: str | None = None,
    is_retry: bool = False,
    drift_report: str | None = None,
    extra_context: list[str] | None = None,
    attachments: list[dict[str, str]] | None = None,
) -> str:
    """Build the prompt piped via stdin to a layer agent.

    MODE B RETRY (--resume): Only rejection feedback. Claude has session history.
    FIRST ATTEMPT / MODE A: Full context from all approved layers below.
    """
    if is_retry:
        findings = ""
        lr = state.layers.get(layer)
        if lr and lr.eval_verdict:
            findings = "\n".join(f"- {f}" for f in lr.eval_verdict.findings)
        return (
            "Your previous output was rejected.\n\n"
            f"User feedback: {user_feedback}\n\n"
            f"Eval findings:\n{findings}\n\n"
            "Revise your output addressing the feedback. Same JSON format."
        )

    parts = [f"# Pipeline Input for {layer.title()} Agent"]
    parts.append(f"\n## Original User Request\n{state.original_prompt}")

    idx = LAYER_ORDER.index(layer)

    for prev in LAYER_ORDER[:idx]:
        lr = state.layers.get(prev)
        if lr and lr.status == "approved" and lr.output:
            parts.append(f"\n## Approved: {prev.title()} Layer")
            if prev == "context":
                parts.append(json.dumps(_slim_context_for_downstream(lr.output), indent=2))
            else:
                parts.append(json.dumps(lr.output, indent=2))
        elif lr and lr.status == "skipped":
            parts.append(f"\n## {prev.title()} Layer: SKIPPED")

    # Include user steering from all previous layers
    if state.user_steering:
        steering_parts = []
        for prev in LAYER_ORDER[:idx]:
            layer_steering = state.user_steering.get(prev)
            if layer_steering:
                for item, response in layer_steering.items():
                    steering_parts.append(f"- **{item}**: {response}")
        if steering_parts:
            parts.append("\n## User Steering (human clarifications from prior layers)")
            parts.extend(steering_parts)

    if user_feedback:
        parts.append(f"\n## Additional Context\n{user_feedback}")

    if drift_report and layer == "coherence":
        parts.append(f"\n## AIQA Drift Report (cross-run quality trends)\n{drift_report}")

    if attachments and layer in ("context", "coherence"):
        parts.append("\n## Attached Files (provided by user)")
        for att in attachments:
            parts.append(f"\n### {att['filename']}")
            parts.append(f"```\n{att['content']}\n```")

    if extra_context:
        parts.append("\n## Additional Context (provided by user)")
        for item in extra_context:
            parts.append(f"- {item}")

    parts.append(f"\n## Your Task\nProduce your {layer} layer output as JSON.")
    return "\n".join(parts)


def build_eval_prompt(
    layer: LayerName,
    layer_output: dict,
    state: PipelineState,
    qa_findings: list[QAFinding] | None = None,
    is_retry: bool = False,
    prior_findings: list[str] | None = None,
) -> str:
    """Build the prompt for the eval agent. Always full context, never resumes."""
    parts = [f"# Evaluate {layer.title()} Layer"]
    parts.append(f"\n## Layer Output\n{json.dumps(layer_output, indent=2)}")

    # Include all previous approved layers for context
    for name in LAYER_ORDER:
        if name == layer:
            break
        lr = state.layers.get(name)
        if lr and lr.status == "approved" and lr.output:
            parts.append(f"\n## Approved: {name.title()} Layer")
            if name == "context" and layer != "context":
                parts.append(json.dumps(_slim_context_for_downstream(lr.output), indent=2))
            else:
                parts.append(json.dumps(lr.output, indent=2))

    parts.append(f"\n## Original Prompt\n{state.original_prompt}")

    if qa_findings:
        failures = [f for f in qa_findings if f.severity == "failure"]
        warnings = [f for f in qa_findings if f.severity == "warning"]
        infos = [f for f in qa_findings if f.severity == "info"]

        parts.append("\n## AIQA Tier 1 Findings (pre-computed structural checks)")
        if failures:
            parts.append("\n### Failures (should cause verdict=fail unless invalid)")
            for f in failures:
                parts.append(f"- [{f.check}] {f.detail}")
        if warnings:
            parts.append("\n### Warnings (investigate whether they indicate real problems)")
            for f in warnings:
                parts.append(f"- [{f.check}] {f.detail}")
        if infos:
            parts.append("\n### Info")
            for f in infos:
                parts.append(f"- [{f.check}] {f.detail}")

    if is_retry and prior_findings:
        parts.append("\n## Prior Eval (this is a RETRY — check if these were addressed)")
        for finding in prior_findings:
            parts.append(f"- {finding}")
        parts.append(
            "\nYour job: determine if the retried output ACTUALLY fixed these issues. "
            "If the same problems persist, verdict should be 'fail', not 'concern'."
        )

    parts.append("\n## Evaluate per criteria and AIQA quality dimensions. Respond with JSON only.")
    return "\n".join(parts)
