"""Immutable pipeline state models and transition functions.

All state objects are frozen Pydantic models. State is never mutated in place —
every transition produces a new state object. Previous states are preserved in history.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Literal

from pydantic import BaseModel, Field

LayerName = Literal["prompt", "context", "intent", "judgment", "coherence"]
LayerStatus = Literal["pending", "running", "approved", "skipped", "rejected", "cleared"]

LAYER_ORDER: list[LayerName] = ["prompt", "context", "intent", "judgment", "coherence"]

MAX_HISTORY_SIZE: int = 20

VALID_TRANSITIONS: dict[str | None, set[LayerStatus]] = {
    None: {"running"},
    "pending": {"running"},
    "running": {"approved", "rejected", "skipped"},
    "approved": {"running", "cleared"},
    "rejected": {"running"},
    "skipped": {"running"},
    "cleared": {"running"},
}


def _validate_transition(current: str | None, target: LayerStatus) -> None:
    """Raise ValueError if the status transition is not allowed."""
    allowed = VALID_TRANSITIONS.get(current, set())
    if target not in allowed:
        raise ValueError(f"Invalid state transition: {current!r} -> {target!r}")


def layer_index(name: LayerName) -> int:
    return LAYER_ORDER.index(name)


def layers_above(name: LayerName) -> list[LayerName]:
    """Return all layers strictly above the given layer."""
    idx = layer_index(name)
    return LAYER_ORDER[idx + 1 :]


def layers_between(from_layer: LayerName, to_layer: LayerName) -> list[LayerName]:
    """Return layers strictly between from_layer and to_layer (exclusive)."""
    lo = layer_index(from_layer)
    hi = layer_index(to_layer)
    if hi <= lo:
        return []
    return LAYER_ORDER[lo + 1 : hi]


# ---------------------------------------------------------------------------
# Data Models
# ---------------------------------------------------------------------------


class UsageRecord(BaseModel, frozen=True):
    """Token usage from a single claude -p call."""

    tokens_in: int
    tokens_out: int
    cache_read_tokens: int = 0
    cache_creation_tokens: int = 0
    model: str
    latency_ms: int
    session_id: str


class EvalVerdict(BaseModel, frozen=True):
    verdict: Literal["pass", "concern", "fail"]
    findings: list[str]
    skip_recommendation: str | None = None
    summary: str
    usage: UsageRecord | None = None


class RejectionContext(BaseModel, frozen=True):
    """Preserved when a layer is reprompted with the same input (Mode B).

    We do NOT store previous_output here for re-injection — --resume gives
    Claude full session history. This exists for the transcript and retry counter.
    """

    user_feedback: str
    eval_verdict: EvalVerdict
    timestamp: datetime
    attempt_number: int


class LayerResult(BaseModel, frozen=True):
    layer: LayerName
    status: LayerStatus
    output: dict | None = None
    eval_verdict: EvalVerdict | None = None
    rejection_history: list[RejectionContext] = Field(default_factory=list)
    usage: UsageRecord | None = None
    session_id: str | None = None
    attempt: int = 1


class PipelineState(BaseModel, frozen=True):
    original_prompt: str
    project_dir: str
    layers: dict[LayerName, LayerResult | None]
    sessions: dict[LayerName, str | None]
    config_snapshot: dict
    run_id: str
    started_at: datetime
    history: list[PipelineState] = Field(default_factory=list)
    user_steering: dict[LayerName, dict[str, str]] = Field(default_factory=dict)


# ---------------------------------------------------------------------------
# State Transition Functions
# ---------------------------------------------------------------------------


def _push_history(state: PipelineState) -> list[PipelineState]:
    """Return a new history list with the current state appended."""
    new_history = list(state.history) + [state.model_copy(update={"history": []})]
    if len(new_history) > MAX_HISTORY_SIZE:
        new_history = new_history[-MAX_HISTORY_SIZE:]
    return new_history


def make_initial_state(
    prompt: str,
    project_dir: str,
    config_snapshot: dict,
    run_id: str,
) -> PipelineState:
    """Create the initial pipeline state with all layers pending."""
    return PipelineState(
        original_prompt=prompt,
        project_dir=project_dir,
        layers={name: None for name in LAYER_ORDER},
        sessions={name: None for name in LAYER_ORDER},
        config_snapshot=config_snapshot,
        run_id=run_id,
        started_at=datetime.now(timezone.utc),
    )


def mark_running(state: PipelineState, layer: LayerName) -> PipelineState:
    """Mark a layer as currently running."""
    new_layers = dict(state.layers)
    existing = new_layers.get(layer)
    _validate_transition(existing.status if existing else None, "running")
    attempt = existing.attempt if existing else 1
    rejection_history = existing.rejection_history if existing else []
    new_layers[layer] = LayerResult(
        layer=layer, status="running", attempt=attempt,
        rejection_history=rejection_history,
    )
    return state.model_copy(update={"layers": new_layers, "history": _push_history(state)})


def approve_layer(
    state: PipelineState,
    layer: LayerName,
    output: dict,
    eval_verdict: EvalVerdict | None,
    usage: UsageRecord | None,
    session_id: str | None,
) -> PipelineState:
    """Mark layer approved, store session_id."""
    existing = state.layers.get(layer)
    _validate_transition(existing.status if existing else None, "approved")
    rejection_history = existing.rejection_history if existing else []
    attempt = existing.attempt if existing else 1

    result = LayerResult(
        layer=layer,
        status="approved",
        output=output,
        eval_verdict=eval_verdict,
        rejection_history=rejection_history,
        usage=usage,
        session_id=session_id,
        attempt=attempt,
    )
    new_layers = dict(state.layers)
    new_layers[layer] = result

    new_sessions = dict(state.sessions)
    new_sessions[layer] = session_id

    return state.model_copy(
        update={
            "layers": new_layers,
            "sessions": new_sessions,
            "history": _push_history(state),
        }
    )


def reject_layer(
    state: PipelineState,
    layer: LayerName,
    eval_verdict: EvalVerdict,
    feedback: str,
) -> PipelineState:
    """Mode B: add rejection context. Session_id PRESERVED for --resume."""
    existing = state.layers.get(layer)
    _validate_transition(existing.status if existing else None, "rejected")
    attempt = existing.attempt if existing else 1
    prev_rejections = list(existing.rejection_history) if existing else []

    rejection = RejectionContext(
        user_feedback=feedback,
        eval_verdict=eval_verdict,
        timestamp=datetime.now(timezone.utc),
        attempt_number=attempt,
    )

    result = LayerResult(
        layer=layer,
        status="rejected",
        output=existing.output if existing else None,
        eval_verdict=eval_verdict,
        rejection_history=prev_rejections + [rejection],
        usage=existing.usage if existing else None,
        session_id=existing.session_id if existing else None,
        attempt=attempt + 1,
    )
    new_layers = dict(state.layers)
    new_layers[layer] = result

    return state.model_copy(
        update={"layers": new_layers, "history": _push_history(state)}
    )


def cascade_reset(
    state: PipelineState,
    from_layer: LayerName,
) -> PipelineState:
    """Mode A: wipe layers + sessions at and above target. History preserved."""
    idx = layer_index(from_layer)
    new_layers = dict(state.layers)
    new_sessions = dict(state.sessions)

    for name in LAYER_ORDER[idx:]:
        new_layers[name] = None
        new_sessions[name] = None

    return state.model_copy(
        update={
            "layers": new_layers,
            "sessions": new_sessions,
            "history": _push_history(state),
        }
    )


def skip_layers(
    state: PipelineState,
    from_layer: LayerName,
    to_layer: LayerName,
) -> PipelineState:
    """Mark intermediate layers as skipped. No sessions created."""
    between = layers_between(from_layer, to_layer)
    new_layers = dict(state.layers)
    for name in between:
        new_layers[name] = LayerResult(layer=name, status="skipped")
    return state.model_copy(
        update={"layers": new_layers, "history": _push_history(state)}
    )
