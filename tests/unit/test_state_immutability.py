"""Tests for state immutability and transition functions."""

import pytest
from datetime import datetime, timezone

from app.state import (
    LAYER_ORDER,
    MAX_HISTORY_SIZE,
    EvalVerdict,
    LayerName,
    LayerResult,
    PipelineState,
    UsageRecord,
    approve_layer,
    cascade_reset,
    layer_index,
    layers_above,
    layers_between,
    make_initial_state,
    mark_running,
    reject_layer,
    skip_layers,
)


@pytest.fixture
def initial_state() -> PipelineState:
    return make_initial_state(
        prompt="test prompt",
        project_dir="/tmp/project",
        config_snapshot={"skip_policy": "recommended"},
        run_id="run_test123",
    )


@pytest.fixture
def sample_usage() -> UsageRecord:
    return UsageRecord(
        tokens_in=100,
        tokens_out=50,
        model="sonnet",
        latency_ms=1000,
        session_id="ses_001",
    )


@pytest.fixture
def sample_verdict() -> EvalVerdict:
    return EvalVerdict(
        verdict="pass",
        findings=["Looks good"],
        summary="All clear",
    )


class TestLayerHelpers:
    def test_layer_index(self):
        assert layer_index("prompt") == 0
        assert layer_index("coherence") == 4

    def test_layers_above(self):
        assert layers_above("prompt") == ["context", "intent", "judgment", "coherence"]
        assert layers_above("coherence") == []

    def test_layers_between(self):
        assert layers_between("prompt", "judgment") == ["context", "intent"]
        assert layers_between("prompt", "context") == []
        assert layers_between("judgment", "prompt") == []


class TestStateImmutability:
    def test_initial_state_is_frozen(self, initial_state):
        with pytest.raises(Exception):
            initial_state.original_prompt = "modified"  # type: ignore

    def test_layer_result_is_frozen(self):
        lr = LayerResult(layer="prompt", status="pending")
        with pytest.raises(Exception):
            lr.status = "running"  # type: ignore

    def test_eval_verdict_is_frozen(self, sample_verdict):
        with pytest.raises(Exception):
            sample_verdict.verdict = "fail"  # type: ignore

    def test_initial_state_has_all_layers_none(self, initial_state):
        for name in LAYER_ORDER:
            assert initial_state.layers[name] is None
            assert initial_state.sessions[name] is None


class TestStateTransitions:
    def test_mark_running_preserves_original(self, initial_state):
        new_state = mark_running(initial_state, "prompt")
        # Original unchanged
        assert initial_state.layers["prompt"] is None
        # New state updated
        assert new_state.layers["prompt"] is not None
        assert new_state.layers["prompt"].status == "running"

    def test_approve_layer(self, initial_state, sample_usage, sample_verdict):
        state = mark_running(initial_state, "prompt")
        state = approve_layer(
            state, "prompt",
            output={"task": "test"},
            eval_verdict=sample_verdict,
            usage=sample_usage,
            session_id="ses_001",
        )
        assert state.layers["prompt"].status == "approved"
        assert state.layers["prompt"].output == {"task": "test"}
        assert state.sessions["prompt"] == "ses_001"

    def test_reject_layer_increments_attempt(self, initial_state, sample_verdict):
        state = mark_running(initial_state, "prompt")
        state = reject_layer(state, "prompt", sample_verdict, "Try again")
        lr = state.layers["prompt"]
        assert lr.status == "rejected"
        assert lr.attempt == 2
        assert len(lr.rejection_history) == 1
        assert lr.rejection_history[0].user_feedback == "Try again"

    def test_cascade_reset_clears_above(self, initial_state, sample_usage, sample_verdict):
        state = initial_state
        # Approve first three layers
        for name in ["prompt", "context", "intent"]:
            state = mark_running(state, name)
            state = approve_layer(
                state, name,
                output={"layer": name},
                eval_verdict=sample_verdict,
                usage=sample_usage,
                session_id=f"ses_{name}",
            )

        # Cascade reset from context
        state = cascade_reset(state, "context")

        assert state.layers["prompt"].status == "approved"  # Below target — preserved
        assert state.layers["context"] is None  # Target — cleared
        assert state.layers["intent"] is None  # Above target — cleared
        assert state.sessions["context"] is None
        assert state.sessions["intent"] is None

    def test_skip_layers(self, initial_state, sample_usage, sample_verdict):
        state = mark_running(initial_state, "prompt")
        state = approve_layer(
            state, "prompt",
            output={"task": "test"},
            eval_verdict=sample_verdict,
            usage=sample_usage,
            session_id="ses_001",
        )

        state = skip_layers(state, "prompt", "judgment")
        assert state.layers["context"].status == "skipped"
        assert state.layers["intent"].status == "skipped"
        # prompt still approved, judgment untouched
        assert state.layers["prompt"].status == "approved"
        assert state.layers["judgment"] is None

    def test_history_preserved(self, initial_state):
        state1 = mark_running(initial_state, "prompt")
        assert len(state1.history) == 1

        state2 = mark_running(state1, "context")
        assert len(state2.history) == 2

    def test_history_capped_at_max(self, initial_state, sample_usage, sample_verdict):
        """History should never exceed MAX_HISTORY_SIZE."""
        state = initial_state
        for _ in range(MAX_HISTORY_SIZE + 10):
            state = mark_running(state, "prompt")
            state = approve_layer(
                state, "prompt",
                output={"task": "test"},
                eval_verdict=sample_verdict,
                usage=sample_usage,
                session_id="ses_001",
            )
            # Reset prompt so we can mark_running again
            state = cascade_reset(state, "prompt")
        assert len(state.history) <= MAX_HISTORY_SIZE


class TestTransitionValidation:
    def test_valid_sequence(self, initial_state, sample_usage, sample_verdict):
        """None -> running -> approved is the normal flow."""
        state = mark_running(initial_state, "prompt")
        state = approve_layer(
            state, "prompt",
            output={"task": "test"},
            eval_verdict=sample_verdict,
            usage=sample_usage,
            session_id="ses_001",
        )
        assert state.layers["prompt"].status == "approved"

    def test_invalid_approve_from_none(self, initial_state, sample_verdict, sample_usage):
        """Cannot approve a layer that hasn't been run."""
        with pytest.raises(ValueError, match="Invalid state transition"):
            approve_layer(
                initial_state, "prompt",
                output={"task": "test"},
                eval_verdict=sample_verdict,
                usage=sample_usage,
                session_id="ses_001",
            )

    def test_invalid_reject_from_none(self, initial_state, sample_verdict):
        """Cannot reject a layer that hasn't been run."""
        with pytest.raises(ValueError, match="Invalid state transition"):
            reject_layer(initial_state, "prompt", sample_verdict, "bad")

    def test_reject_from_running(self, initial_state, sample_verdict):
        """Running -> rejected is the normal reprompt flow."""
        state = mark_running(initial_state, "prompt")
        state = reject_layer(state, "prompt", sample_verdict, "Try again")
        assert state.layers["prompt"].status == "rejected"

    def test_rerun_after_reject(self, initial_state, sample_verdict):
        """Rejected -> running is allowed for retries."""
        state = mark_running(initial_state, "prompt")
        state = reject_layer(state, "prompt", sample_verdict, "Try again")
        state = mark_running(state, "prompt")
        assert state.layers["prompt"].status == "running"
