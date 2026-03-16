"""Adversarial tests for pipeline configuration and edge cases."""

import pytest

from app.config import PipelineConfig, load_config
from app.state import (
    LAYER_ORDER,
    EvalVerdict,
    UsageRecord,
    approve_layer,
    cascade_reset,
    make_initial_state,
    mark_running,
    reject_layer,
)


class TestRetryLimits:
    def test_reject_tracks_attempts(self):
        verdict = EvalVerdict(verdict="fail", findings=["bad"], summary="nope")
        state = make_initial_state("test", "/tmp", {}, "run_adv")

        # Simulate the real pipeline flow: mark_running -> reject -> mark_running -> reject ...
        for i in range(3):
            state = mark_running(state, "prompt")
            state = reject_layer(state, "prompt", verdict, f"Fix attempt {i+1}")

        lr = state.layers["prompt"]
        assert lr.attempt == 4  # Original + 3 rejections
        assert len(lr.rejection_history) == 3

    def test_config_max_retries(self):
        config = load_config()
        assert config.max_retries_per_layer >= 1
        assert config.max_retries_per_layer <= 10


class TestEdgeCases:
    def test_cascade_reset_from_first_layer(self):
        state = make_initial_state("test", "/tmp", {}, "run_edge")
        verdict = EvalVerdict(verdict="pass", findings=[], summary="ok")
        usage = UsageRecord(
            tokens_in=100, tokens_out=50, model="haiku",
            latency_ms=500, session_id="ses_1",
        )

        state = mark_running(state, "prompt")
        state = approve_layer(
            state, "prompt", {"task": "test"}, verdict, usage, "ses_1"
        )

        # Cascade from prompt = clear everything
        state = cascade_reset(state, "prompt")
        for name in LAYER_ORDER:
            assert state.layers[name] is None

    def test_double_approve_requires_rerun(self):
        """Re-approving a layer requires going through running state first."""
        state = make_initial_state("test", "/tmp", {}, "run_idem")
        verdict = EvalVerdict(verdict="pass", findings=[], summary="ok")

        state = mark_running(state, "prompt")
        state = approve_layer(
            state, "prompt", {"v": 1}, verdict, None, "ses_1"
        )
        # Must go through running again to re-approve
        state = mark_running(state, "prompt")
        state = approve_layer(
            state, "prompt", {"v": 2}, verdict, None, "ses_2"
        )

        assert state.layers["prompt"].output == {"v": 2}
        assert state.sessions["prompt"] == "ses_2"

    def test_empty_layers_dict_uses_defaults(self):
        config = PipelineConfig()
        layer = config.get_layer("prompt")
        assert layer.enabled is True
        assert layer.model == "sonnet"  # Default

    def test_skip_policy_never_config(self):
        config = load_config(cli_overrides={"skip_policy": "never"})
        assert config.skip_policy == "never"

    def test_auto_gate_config(self):
        config = load_config(cli_overrides={"eval_gate": "auto"})
        assert config.eval_gate == "auto"
