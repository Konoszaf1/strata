"""Core pipeline orchestrator — sequential layer execution with gated transitions.

Runs five cognitive layers as isolated claude -p sessions, with eval between each.
Supports Mode A (cascade reset), Mode B (reprompt with --resume), skip logic,
and auto/human eval gating.
"""

from __future__ import annotations

import json
import logging
import uuid
from enum import Enum
from pathlib import Path
from typing import Awaitable, Callable, Literal

from pydantic import BaseModel

from app.agents.prompts import build_eval_prompt, build_layer_prompt
from app.agents.runner import ClaudeCliError, LayerCancelled, RateLimitError, run_claude, _parse_usage
from app.agents.validation import validate_resumed_session
from app.config import PipelineConfig, resolve_harness_dir
from app.qa.drift import analyze_drift
from app.qa.validators import QAFinding, validate_layer_output
from app.state import (
    LAYER_ORDER,
    EvalVerdict,
    LayerName,
    PipelineState,
    UsageRecord,
    approve_layer,
    cascade_reset,
    layer_index,
    make_initial_state,
    mark_running,
    reject_layer,
    skip_layers,
)

logger = logging.getLogger(__name__)


class CheckpointAction(str, Enum):
    APPROVE = "approve"
    SKIP_TO = "skip_to"
    REPROMPT_CURRENT = "reprompt_current"
    REPROMPT_LOWER = "reprompt_lower"
    ABORT = "abort"


class CheckpointEvent(BaseModel):
    layer: LayerName
    layer_output: dict
    eval_verdict: EvalVerdict
    state: PipelineState
    available_actions: list[CheckpointAction]
    skip_suggestion: LayerName | None = None
    is_auto_approved: bool = False
    eval_failed: bool = False
    rate_limit_warning: str | None = None


class UserDecision(BaseModel):
    action: CheckpointAction
    target_layer: LayerName | None = None
    feedback: str | None = None


def _available_actions(
    layer: LayerName,
    config: PipelineConfig,
    state: PipelineState,
) -> list[CheckpointAction]:
    """Determine which actions the user can take at this checkpoint."""
    actions = [CheckpointAction.APPROVE]

    # Skip is available if skip_policy allows and there are layers above
    idx = layer_index(layer)
    if idx < len(LAYER_ORDER) - 1 and config.skip_policy != "never":
        actions.append(CheckpointAction.SKIP_TO)

    # Reprompt current layer if retries remain
    lr = state.layers.get(layer)
    attempt = lr.attempt if lr else 1
    if attempt < config.max_retries_per_layer:
        actions.append(CheckpointAction.REPROMPT_CURRENT)

    # Cascade back to lower layer if not at the bottom
    if idx > 0:
        actions.append(CheckpointAction.REPROMPT_LOWER)

    actions.append(CheckpointAction.ABORT)
    return actions


def _should_auto_approve(
    verdict: EvalVerdict,
    config: PipelineConfig,
) -> bool:
    """Check if this layer should be auto-approved based on config."""
    if config.eval_gate != "auto":
        return False
    # Auto-approve only on pass; concern/fail escalate to human
    return verdict.verdict == "pass"


def _parse_agent_output(raw_result: dict) -> dict:
    """Extract the structured JSON output from a claude -p response.

    The 'result' field contains the agent's text output, which should be
    a JSON object per our output control rules. Handle cases where Claude
    wraps it in markdown fences.
    """
    text = raw_result.get("result", "")
    if not text:
        return {"_raw": "", "_parse_error": "Empty result from agent"}

    # Strip markdown fences if present
    cleaned = text.strip()
    if cleaned.startswith("```"):
        lines = cleaned.split("\n")
        # Remove first line (```json or ```) and last line (```)
        if lines[-1].strip() == "```":
            lines = lines[1:-1]
        else:
            lines = lines[1:]
        cleaned = "\n".join(lines).strip()

    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        return {"_raw": text, "_parse_error": "Agent did not return valid JSON"}


def _parse_eval_output(raw_result: dict) -> EvalVerdict:
    """Parse eval agent output into an EvalVerdict."""
    parsed = _parse_agent_output(raw_result)
    usage = _parse_usage(raw_result)

    if "_parse_error" in parsed:
        return EvalVerdict(
            verdict="concern",
            findings=[f"Eval parse error: {parsed['_parse_error']}"],
            summary="Could not parse eval output",
            usage=usage,
        )

    return EvalVerdict(
        verdict=parsed.get("verdict", "concern"),
        findings=parsed.get("findings", []),
        skip_recommendation=parsed.get("skip_recommendation"),
        summary=parsed.get("summary", "No summary"),
        usage=usage,
    )


async def _run_layer(
    layer: LayerName,
    state: PipelineState,
    config: PipelineConfig,
    harness_dir: Path,
    is_retry: bool = False,
    user_feedback: str | None = None,
    drift_report: str | None = None,
    extra_context: list[str] | None = None,
    attachments: list[dict[str, str]] | None = None,
) -> tuple[dict, dict]:
    """Execute a single layer agent. Returns (parsed_output, raw_result)."""
    layer_cfg = config.get_layer(layer)
    agent_file = harness_dir / "agents" / f"{layer_index(layer) + 1}-{layer}.md"

    if not agent_file.is_file():
        raise FileNotFoundError(f"Agent prompt file not found: {agent_file}")

    prompt = build_layer_prompt(
        layer, state, user_feedback=user_feedback, is_retry=is_retry,
        drift_report=drift_report,
        extra_context=extra_context,
        attachments=attachments,
    )

    # Determine if we should resume an existing session
    resume_session = None
    if is_retry and state.sessions.get(layer):
        resume_session = state.sessions[layer]

    # Only coherence gets --setting-sources
    setting_sources = layer_cfg.setting_sources if layer == "coherence" else None

    raw_result = await run_claude(
        prompt=prompt,
        append_system_prompt_file=agent_file,
        model=layer_cfg.model,
        max_turns=layer_cfg.max_turns,
        allowed_tools=layer_cfg.allowed_tools,
        project_dir=state.project_dir,
        resume_session=resume_session,
        setting_sources=setting_sources,
    )

    # Validate session if this was a resume
    if resume_session:
        prev_lr = state.layers.get(layer)
        prev_usage = prev_lr.usage if prev_lr else None
        is_valid, reason = validate_resumed_session(
            raw_result, resume_session, prev_usage,
            token_threshold=config.session_resume_token_threshold,
            enabled=config.session_resume_validation,
        )
        if not is_valid:
            logger.warning(
                "Session resume failed for %s: %s. Falling back to fresh session.",
                layer, reason,
            )
            # Re-run as fresh session with full context + retry feedback
            full_prompt = build_layer_prompt(
                layer, state, user_feedback=user_feedback, is_retry=False
            )
            if user_feedback:
                full_prompt += (
                    f"\n\n## Note: This is a retry. Previous attempt was rejected.\n"
                    f"{user_feedback}"
                )
            raw_result = await run_claude(
                prompt=full_prompt,
                append_system_prompt_file=agent_file,
                model=layer_cfg.model,
                max_turns=layer_cfg.max_turns,
                allowed_tools=layer_cfg.allowed_tools,
                project_dir=state.project_dir,
                resume_session=None,
                setting_sources=setting_sources,
            )

    parsed = _parse_agent_output(raw_result)

    # Empty-result recovery: if the agent used all turns on tool calls and never
    # emitted a final JSON response, resume the session with a nudge to produce output.
    if "_parse_error" in parsed and not raw_result.get("result", "").strip():
        session_id = raw_result.get("session_id")
        if session_id:
            logger.warning(
                "Layer %s returned empty result (likely exhausted turns on tool calls). "
                "Resuming session to collect JSON output.",
                layer,
            )
            nudge_result = await run_claude(
                prompt=(
                    "You used all your turns on tool calls without producing output. "
                    "Now produce your JSON response based on what you found. "
                    "Respond with ONLY the JSON object, nothing else."
                ),
                append_system_prompt_file=agent_file,
                model=layer_cfg.model,
                max_turns=2,
                allowed_tools=[],  # No tools — force text output
                project_dir=state.project_dir,
                resume_session=session_id,
                setting_sources=setting_sources,
            )
            nudge_parsed = _parse_agent_output(nudge_result)
            if "_parse_error" not in nudge_parsed:
                return nudge_parsed, nudge_result
            # If nudge also failed, fall through with original error
            logger.warning("Empty-result recovery also failed for %s", layer)

    # Malformed-JSON recovery: if agent returned text but it's not valid JSON,
    # resume the session and ask for clean JSON output.
    if "_parse_error" in parsed and raw_result.get("result", "").strip():
        session_id = raw_result.get("session_id")
        if session_id:
            logger.warning(
                "Layer %s returned non-JSON text. Resuming session to request clean JSON.",
                layer,
            )
            nudge_result = await run_claude(
                prompt=(
                    "Your previous response was not valid JSON. "
                    "Respond with ONLY the JSON object specified in your instructions. "
                    "No markdown fences, no commentary, no explanation — just the raw JSON."
                ),
                append_system_prompt_file=agent_file,
                model=layer_cfg.model,
                max_turns=2,
                allowed_tools=[],  # No tools — force text output
                project_dir=state.project_dir,
                resume_session=session_id,
                setting_sources=setting_sources,
            )
            nudge_parsed = _parse_agent_output(nudge_result)
            if "_parse_error" not in nudge_parsed:
                return nudge_parsed, nudge_result
            logger.warning("Malformed-JSON recovery also failed for %s", layer)

    return parsed, raw_result


async def _run_eval(
    layer: LayerName,
    layer_output: dict,
    state: PipelineState,
    config: PipelineConfig,
    harness_dir: Path,
    qa_findings: list[QAFinding] | None = None,
    is_retry: bool = False,
    prior_findings: list[str] | None = None,
) -> EvalVerdict:
    """Run the eval agent (always fresh session)."""
    eval_file = harness_dir / "agents" / "eval.md"
    if not eval_file.is_file():
        raise FileNotFoundError(f"Eval prompt file not found: {eval_file}")

    prompt = build_eval_prompt(
        layer, layer_output, state, qa_findings=qa_findings,
        is_retry=is_retry, prior_findings=prior_findings,
    )

    raw_result = await run_claude(
        prompt=prompt,
        append_system_prompt_file=eval_file,
        model=config.eval.model,
        max_turns=config.eval.max_turns,
        allowed_tools=config.eval.allowed_tools,
        project_dir=state.project_dir,
        resume_session=None,  # Always fresh
    )

    return _parse_eval_output(raw_result)


class InterruptEvent(BaseModel):
    """Presented when user interrupts a running layer with Ctrl+C."""
    layer: LayerName
    state: PipelineState
    can_go_back: bool
    previous_layer: LayerName | None = None


class InterruptDecision(BaseModel):
    action: Literal["retry", "back", "abort"]
    target_layer: LayerName | None = None


async def _collect_steering(
    state: PipelineState,
    layer: LayerName,
    layer_output: dict,
    on_steering: Callable[[LayerName, dict], Awaitable[dict[str, str]]],
) -> PipelineState:
    """Collect user steering for a layer's output and store it in state."""
    responses = await on_steering(layer, layer_output)
    actual = {k: v for k, v in responses.items() if v}
    if not actual:
        return state
    new_steering = dict(state.user_steering)
    new_steering[layer] = actual
    return state.model_copy(update={"user_steering": new_steering})


async def run_pipeline(
    user_prompt: str,
    config: PipelineConfig,
    project_dir: str,
    on_checkpoint: Callable[[CheckpointEvent], Awaitable[UserDecision]],
    on_layer_start: Callable[[LayerName, int], Awaitable[None]] | None = None,
    on_eval_start: Callable[[LayerName], Awaitable[None]] | None = None,
    on_auto_approve: Callable[[LayerName, EvalVerdict], Awaitable[None]] | None = None,
    on_interrupt: Callable[[InterruptEvent], Awaitable[InterruptDecision]] | None = None,
    on_steering: Callable[[LayerName, dict], Awaitable[dict[str, str]]] | None = None,
    harness_override: str | None = None,
    usage_tracker: object | None = None,
    run_id: str | None = None,
    extra_context: dict[str, list[str]] | None = None,
    attachments: list[dict[str, str]] | None = None,
) -> PipelineState:
    """Main pipeline loop.

    Parameters
    ----------
    user_prompt:
        The user's raw prompt.
    config:
        Merged pipeline configuration.
    project_dir:
        Absolute path to the project (cwd where user invoked the app).
    on_checkpoint:
        Callback for human gating. Receives CheckpointEvent, returns UserDecision.
    on_layer_start:
        Optional callback when a layer begins (for UI).
    on_eval_start:
        Optional callback when eval begins for a layer (for UI).
    on_auto_approve:
        Optional callback when a layer is auto-approved (for UI).
    on_steering:
        Optional callback to collect user steering after each layer approval.
        Receives (layer_name, layer_output), returns dict of item→response.
    harness_override:
        Optional custom harness directory path.
    usage_tracker:
        Optional UsageTracker for rate limit awareness.
    """
    harness_dir = resolve_harness_dir(harness_override)
    run_id = run_id or f"run_{uuid.uuid4().hex[:12]}"

    state = make_initial_state(
        prompt=user_prompt,
        project_dir=project_dir,
        config_snapshot=config.model_dump(),
        run_id=run_id,
    )

    # Determine which layers to run
    enabled_layers = [
        name for name in LAYER_ORDER if config.get_layer(name).enabled
    ]

    # Circuit breaker: abort if consecutive CLI failures suggest systemic issue
    consecutive_failures = 0
    max_consecutive_failures = 3

    i = 0
    while i < len(enabled_layers):
        layer = enabled_layers[i]

        # Check if already approved (can happen after cascade reset partial re-run)
        lr = state.layers.get(layer)
        if lr and lr.status == "approved":
            i += 1
            continue

        # Check if this is a retry
        is_retry = lr is not None and lr.status == "rejected"
        attempt = lr.attempt if lr else 1

        if on_layer_start:
            await on_layer_start(layer, attempt)

        pre_layer_state = state
        state = mark_running(state, layer)

        # AIQA Tier 3: inject drift report for coherence layer
        drift_text = None
        if layer == "coherence":
            drift = analyze_drift(project_dir)
            if drift.quality_trend != "insufficient_data":
                drift_parts = [
                    f"Trend: {drift.quality_trend} (across {drift.run_count} runs)",
                    drift.recommendation,
                ]
                if drift.recurring_findings:
                    drift_parts.append("Recurring findings: " + "; ".join(drift.recurring_findings[:3]))
                drift_text = "\n".join(drift_parts)

        # Resolve per-layer extra context
        layer_extra = (extra_context or {}).get(layer)

        # Run the layer agent
        try:
            layer_output, raw_result = await _run_layer(
                layer=layer,
                state=state,
                config=config,
                harness_dir=harness_dir,
                is_retry=is_retry,
                user_feedback=lr.rejection_history[-1].user_feedback if is_retry and lr and lr.rejection_history else None,
                drift_report=drift_text,
                extra_context=layer_extra,
                attachments=attachments,
            )
        except LayerCancelled:
            logger.info("Layer %s cancelled by user", layer)
            # Revert to the known-good state before mark_running()
            state = pre_layer_state
            # Ask user what to do
            if on_interrupt:
                prev_layer = enabled_layers[i - 1] if i > 0 else None
                int_event = InterruptEvent(
                    layer=layer,
                    state=state,
                    can_go_back=i > 0,
                    previous_layer=prev_layer,
                )
                int_decision = await on_interrupt(int_event)
                if int_decision.action == "retry":
                    continue  # Re-run same layer
                elif int_decision.action == "back":
                    target = int_decision.target_layer or prev_layer
                    if target and target in enabled_layers:
                        state = cascade_reset(state, target)
                        i = enabled_layers.index(target)
                    continue
                else:  # abort
                    return state
            else:
                return state
        except ClaudeCliError as exc:
            if "Timed out" in str(exc):
                logger.warning("Layer %s timed out: %s", layer, exc)
                state = pre_layer_state
                if on_interrupt:
                    prev_layer = enabled_layers[i - 1] if i > 0 else None
                    int_event = InterruptEvent(
                        layer=layer,
                        state=state,
                        can_go_back=i > 0,
                        previous_layer=prev_layer,
                    )
                    int_decision = await on_interrupt(int_event)
                    if int_decision.action == "retry":
                        continue
                    elif int_decision.action == "back":
                        target = int_decision.target_layer or prev_layer
                        if target and target in enabled_layers:
                            state = cascade_reset(state, target)
                            i = enabled_layers.index(target)
                        continue
                    else:
                        return state
                else:
                    raise
            logger.error("Layer %s failed: %s", layer, exc)
            raise
        except FileNotFoundError as exc:
            logger.error("Layer %s failed: %s", layer, exc)
            raise

        session_id = raw_result.get("session_id")
        usage = _parse_usage(raw_result)

        # Record usage
        if usage_tracker and usage:
            usage_tracker.record_usage(layer, usage.model, usage.tokens_in, usage.tokens_out)

        # AIQA Tier 1: structural validation before eval
        qa_findings = validate_layer_output(layer, layer_output, state, project_dir)
        tier1_failures = [f for f in qa_findings if f.severity == "failure"]
        if tier1_failures:
            logger.warning(
                "AIQA Tier 1 found %d failure(s) for %s: %s",
                len(tier1_failures), layer,
                "; ".join(f.detail for f in tier1_failures),
            )

        # Extract prior eval findings for retry awareness
        is_layer_retry = lr is not None and lr.attempt > 1
        prior_eval_findings = None
        if is_layer_retry and lr and lr.rejection_history:
            last_rejection = lr.rejection_history[-1]
            if last_rejection.eval_verdict:
                prior_eval_findings = last_rejection.eval_verdict.findings

        # Run eval
        eval_failed = False
        if on_eval_start:
            await on_eval_start(layer)
        try:
            eval_verdict = await _run_eval(
                layer=layer,
                layer_output=layer_output,
                state=state,
                config=config,
                harness_dir=harness_dir,
                qa_findings=qa_findings,
                is_retry=is_layer_retry,
                prior_findings=prior_eval_findings,
            )
        except LayerCancelled:
            logger.info("Eval for %s cancelled by user", layer)
            # Eval cancelled — treat as concern, show checkpoint anyway
            eval_verdict = EvalVerdict(
                verdict="concern",
                findings=["Eval was interrupted by user"],
                summary="Eval skipped — interrupted",
            )
        except FileNotFoundError:
            raise  # Fatal config error — missing eval.md
        except ClaudeCliError as exc:
            consecutive_failures += 1
            if consecutive_failures >= max_consecutive_failures:
                raise ClaudeCliError(
                    f"Pipeline aborted: {consecutive_failures} consecutive CLI failures "
                    f"(last: {exc})",
                    -1,
                ) from exc
            logger.warning("Eval for %s failed: %s", layer, exc)
            eval_failed = True
            eval_verdict = EvalVerdict(
                verdict="concern",
                findings=[f"Eval agent error: {exc}"],
                summary="Eval failed — manual review recommended",
            )

        # Reset circuit breaker on successful eval
        if not eval_failed:
            consecutive_failures = 0

        # Record eval usage
        if usage_tracker and eval_verdict.usage:
            usage_tracker.record_usage(
                f"eval_{layer}",
                eval_verdict.usage.model,
                eval_verdict.usage.tokens_in,
                eval_verdict.usage.tokens_out,
            )

        # Judgment "reconsider" forces human gate regardless of config
        force_human_gate = (
            layer == "judgment"
            and layer_output.get("go_no_go") == "reconsider"
        )
        if force_human_gate:
            logger.warning("Judgment recommends 'reconsider' — forcing human gate")

        # Check for auto-approve
        if _should_auto_approve(eval_verdict, config) and not force_human_gate:
            state = approve_layer(state, layer, layer_output, eval_verdict, usage, session_id)
            if on_auto_approve:
                await on_auto_approve(layer, eval_verdict)
            # Resolve ambiguities after prompt layer approval
            if on_steering and layer != "coherence":
                state = await _collect_steering(state, layer, layer_output, on_steering)
            i += 1
            continue

        # Human checkpoint
        rate_warning = None
        if usage_tracker:
            pct = usage_tracker.estimate_remaining_pct(config.plan)
            if pct < 0.3:
                rate_warning = f"~{int((1 - pct) * 100)}% of hourly budget used"

        event = CheckpointEvent(
            layer=layer,
            layer_output=layer_output,
            eval_verdict=eval_verdict,
            state=state,
            available_actions=_available_actions(layer, config, state),
            skip_suggestion=eval_verdict.skip_recommendation,
            eval_failed=eval_failed,
            rate_limit_warning=rate_warning,
        )

        decision = await on_checkpoint(event)

        # Handle decision
        if decision.action == CheckpointAction.APPROVE:
            state = approve_layer(state, layer, layer_output, eval_verdict, usage, session_id)
            # Resolve ambiguities after prompt layer approval
            if on_steering and layer != "coherence":
                state = await _collect_steering(state, layer, layer_output, on_steering)
            i += 1

        elif decision.action == CheckpointAction.SKIP_TO:
            target = decision.target_layer
            if target and target in enabled_layers:
                state = approve_layer(state, layer, layer_output, eval_verdict, usage, session_id)
                state = skip_layers(state, layer, target)
                i = enabled_layers.index(target)
            else:
                # Invalid skip target, treat as approve
                state = approve_layer(state, layer, layer_output, eval_verdict, usage, session_id)
                i += 1

        elif decision.action == CheckpointAction.REPROMPT_CURRENT:
            # Mode B — reject and retry. Session preserved for --resume.
            feedback = decision.feedback or "Please revise your output."
            state = reject_layer(state, layer, eval_verdict, feedback)
            # Don't increment i — will re-run this layer

        elif decision.action == CheckpointAction.REPROMPT_LOWER:
            # Mode A — cascade reset back to target layer
            target = decision.target_layer
            if target and target in enabled_layers:
                target_idx = enabled_layers.index(target)
                state = cascade_reset(state, target)
                i = target_idx
            else:
                # Fall back to one layer below
                if i > 0:
                    prev = enabled_layers[i - 1]
                    state = cascade_reset(state, prev)
                    i = i - 1

        elif decision.action == CheckpointAction.ABORT:
            logger.info("Pipeline aborted by user at layer %s", layer)
            return state

    return state
