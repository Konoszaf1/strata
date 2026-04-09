"""Tests for auto-gating and skip policy logic."""

import pytest

from app.config import PipelineConfig
from app.pipeline import CheckpointAction, _available_actions, _should_auto_approve
from app.state import (
    EvalVerdict,
    LayerResult,
    PipelineState,
    make_initial_state,
    approve_layer,
    mark_running,
    skip_layers,
    UsageRecord,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _verdict(v: str = "pass", skip: str | None = None) -> EvalVerdict:
    return EvalVerdict(verdict=v, findings=[], summary="test", skip_recommendation=skip)


def _config(**overrides) -> PipelineConfig:
    return PipelineConfig(**overrides)


def _initial_state() -> PipelineState:
    return make_initial_state("test prompt", "/tmp", {}, "test-run")


def _state_with_attempt(layer: str, attempt: int) -> PipelineState:
    """Create a state where the given layer has a specific attempt count."""
    state = _initial_state()
    layers = dict(state.layers)
    layers[layer] = LayerResult(layer=layer, status="running", attempt=attempt)
    return state.model_copy(update={"layers": layers})


# ---------------------------------------------------------------------------
# _should_auto_approve
# ---------------------------------------------------------------------------

class TestShouldAutoApprove:
    def test_auto_gate_pass_approves(self):
        assert _should_auto_approve(_verdict("pass"), _config(eval_gate="auto")) is True

    def test_auto_gate_concern_escalates(self):
        assert _should_auto_approve(_verdict("concern"), _config(eval_gate="auto")) is False

    def test_auto_gate_fail_escalates(self):
        assert _should_auto_approve(_verdict("fail"), _config(eval_gate="auto")) is False

    def test_human_gate_pass_does_not_auto_approve(self):
        assert _should_auto_approve(_verdict("pass"), _config(eval_gate="human")) is False

    def test_human_gate_fail_does_not_auto_approve(self):
        assert _should_auto_approve(_verdict("fail"), _config(eval_gate="human")) is False


# ---------------------------------------------------------------------------
# _available_actions
# ---------------------------------------------------------------------------

class TestAvailableActions:
    """Test action availability based on skip_policy, layer position, and retries."""

    def test_skip_never_hides_skip(self):
        state = _initial_state()
        config = _config(skip_policy="never")
        actions = _available_actions("prompt", config, state)
        assert CheckpointAction.SKIP_TO not in actions

    def test_skip_next_shows_skip(self):
        state = _initial_state()
        config = _config(skip_policy="next")
        actions = _available_actions("prompt", config, state)
        assert CheckpointAction.SKIP_TO in actions

    def test_skip_recommended_shows_skip(self):
        state = _initial_state()
        config = _config(skip_policy="recommended")
        actions = _available_actions("prompt", config, state)
        assert CheckpointAction.SKIP_TO in actions

    def test_skip_always_shows_skip(self):
        state = _initial_state()
        config = _config(skip_policy="always")
        actions = _available_actions("prompt", config, state)
        assert CheckpointAction.SKIP_TO in actions

    def test_coherence_cannot_skip(self):
        """Last layer has nothing to skip to."""
        state = _initial_state()
        config = _config(skip_policy="recommended")
        actions = _available_actions("coherence", config, state)
        assert CheckpointAction.SKIP_TO not in actions

    def test_prompt_cannot_go_back(self):
        """First layer has nothing to go back to."""
        state = _initial_state()
        config = _config()
        actions = _available_actions("prompt", config, state)
        assert CheckpointAction.REPROMPT_LOWER not in actions

    def test_intent_can_go_back(self):
        state = _initial_state()
        config = _config()
        actions = _available_actions("intent", config, state)
        assert CheckpointAction.REPROMPT_LOWER in actions

    def test_max_retries_hides_reprompt(self):
        """When attempt == max_retries, reprompt_current is unavailable."""
        config = _config(max_retries_per_layer=3)
        state = _state_with_attempt("prompt", attempt=3)
        actions = _available_actions("prompt", config, state)
        assert CheckpointAction.REPROMPT_CURRENT not in actions

    def test_under_max_retries_shows_reprompt(self):
        config = _config(max_retries_per_layer=3)
        state = _state_with_attempt("prompt", attempt=2)
        actions = _available_actions("prompt", config, state)
        assert CheckpointAction.REPROMPT_CURRENT in actions

    def test_approve_and_abort_always_available(self):
        state = _initial_state()
        config = _config()
        for layer in ["prompt", "context", "intent", "judgment", "coherence"]:
            actions = _available_actions(layer, config, state)
            assert CheckpointAction.APPROVE in actions
            assert CheckpointAction.ABORT in actions


# ---------------------------------------------------------------------------
# Skip state transitions
# ---------------------------------------------------------------------------

class TestSkipStateTransitions:
    _usage = UsageRecord(tokens_in=1, tokens_out=1, cache_read_tokens=0,
                         cache_creation_tokens=0, model="sonnet",
                         latency_ms=100, session_id="s1")

    def _approve(self, state, layer):
        """Mark running then approve — respects valid state transitions."""
        state = mark_running(state, layer)
        return approve_layer(state, layer, {"task": "test"}, _verdict("pass"), self._usage, "s1")

    def test_skip_marks_intermediate_as_skipped(self):
        state = _initial_state()
        state = self._approve(state, "prompt")
        state = skip_layers(state, "prompt", "judgment")

        assert state.layers["context"].status == "skipped"
        assert state.layers["intent"].status == "skipped"
        # Judgment should NOT be marked skipped — it's the target to run
        assert state.layers["judgment"] is None or state.layers["judgment"].status != "skipped"

    def test_skip_adjacent_skips_nothing(self):
        state = _initial_state()
        state = self._approve(state, "prompt")
        state = skip_layers(state, "prompt", "context")

        # No layers between prompt and context
        assert state.layers["context"] is None  # untouched, ready to run

    def test_skip_preserves_approved_layers(self):
        state = _initial_state()
        state = self._approve(state, "prompt")
        state = skip_layers(state, "prompt", "coherence")

        # Prompt should remain approved
        assert state.layers["prompt"].status == "approved"
        # Intermediate layers skipped
        assert state.layers["context"].status == "skipped"
        assert state.layers["intent"].status == "skipped"
        assert state.layers["judgment"].status == "skipped"
