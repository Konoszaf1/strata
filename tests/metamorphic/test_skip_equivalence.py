"""Metamorphic test: skipping layers should produce compensated output.

When layers are skipped, Coherence must compensate. This test verifies the
skip logic and state transitions work correctly so Coherence has the
information it needs to compensate.
"""

import pytest

from app.state import (
    LAYER_ORDER,
    EvalVerdict,
    UsageRecord,
    approve_layer,
    make_initial_state,
    mark_running,
    skip_layers,
)


@pytest.fixture
def state_after_prompt():
    verdict = EvalVerdict(verdict="pass", findings=[], summary="ok")
    usage = UsageRecord(
        tokens_in=100, tokens_out=50, model="haiku",
        latency_ms=500, session_id="ses_p",
    )
    state = make_initial_state("test", "/tmp/proj", {}, "run_skip")
    state = mark_running(state, "prompt")
    return approve_layer(
        state, "prompt",
        output={"task_description": "test", "complexity": {"level": "low"}},
        eval_verdict=verdict,
        usage=usage,
        session_id="ses_p",
    )


class TestSkipEquivalence:
    def test_skip_marks_intermediate_layers(self, state_after_prompt):
        state = skip_layers(state_after_prompt, "prompt", "coherence")
        assert state.layers["context"].status == "skipped"
        assert state.layers["intent"].status == "skipped"
        assert state.layers["judgment"].status == "skipped"
        # Coherence itself is NOT marked (it will be run)
        assert state.layers["coherence"] is None

    def test_skip_preserves_prompt_output(self, state_after_prompt):
        state = skip_layers(state_after_prompt, "prompt", "coherence")
        assert state.layers["prompt"].status == "approved"
        assert state.layers["prompt"].output is not None

    def test_skip_to_adjacent_skips_nothing(self, state_after_prompt):
        state = skip_layers(state_after_prompt, "prompt", "context")
        # No layers between prompt and context
        assert state.layers["context"] is None  # Not skipped, just not run yet

    def test_coherence_knows_what_was_skipped(self, state_after_prompt):
        """Coherence should be able to detect skipped layers from state."""
        state = skip_layers(state_after_prompt, "prompt", "coherence")

        skipped = [
            name for name in LAYER_ORDER
            if state.layers.get(name) and state.layers[name].status == "skipped"
        ]
        assert skipped == ["context", "intent", "judgment"]
