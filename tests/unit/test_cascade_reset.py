"""Tests for cascade reset (Mode A) behavior."""

import pytest

from app.state import (
    LAYER_ORDER,
    EvalVerdict,
    UsageRecord,
    approve_layer,
    cascade_reset,
    make_initial_state,
    mark_running,
)


@pytest.fixture
def fully_approved_state():
    """State where all 5 layers are approved."""
    verdict = EvalVerdict(verdict="pass", findings=[], summary="ok")
    usage = UsageRecord(
        tokens_in=100, tokens_out=50, model="sonnet",
        latency_ms=1000, session_id="ses_test",
    )
    state = make_initial_state("test", "/tmp/proj", {}, "run_test")

    for name in LAYER_ORDER:
        state = mark_running(state, name)
        state = approve_layer(
            state, name,
            output={"layer": name},
            eval_verdict=verdict,
            usage=usage,
            session_id=f"ses_{name}",
        )
    return state


class TestCascadeReset:
    def test_cascade_from_bottom_clears_everything(self, fully_approved_state):
        state = cascade_reset(fully_approved_state, "prompt")
        for name in LAYER_ORDER:
            assert state.layers[name] is None
            assert state.sessions[name] is None

    def test_cascade_from_middle_preserves_below(self, fully_approved_state):
        state = cascade_reset(fully_approved_state, "intent")

        # Below target — preserved
        assert state.layers["prompt"].status == "approved"
        assert state.layers["context"].status == "approved"
        assert state.sessions["prompt"] == "ses_prompt"
        assert state.sessions["context"] == "ses_context"

        # Target and above — cleared
        for name in ["intent", "judgment", "coherence"]:
            assert state.layers[name] is None
            assert state.sessions[name] is None

    def test_cascade_from_top_only_clears_top(self, fully_approved_state):
        state = cascade_reset(fully_approved_state, "coherence")

        for name in LAYER_ORDER[:-1]:
            assert state.layers[name].status == "approved"

        assert state.layers["coherence"] is None
        assert state.sessions["coherence"] is None

    def test_cascade_preserves_history(self, fully_approved_state):
        state = cascade_reset(fully_approved_state, "context")
        # History should include the pre-reset state
        assert len(state.history) > 0
        # The last history entry should have all layers approved
        last = state.history[-1]
        assert last.layers["context"].status == "approved"
