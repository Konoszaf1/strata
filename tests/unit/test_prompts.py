"""Tests for prompt construction (context slimming, eval retry memory)."""

import json

import pytest

from app.agents.prompts import (
    _slim_context_for_downstream,
    build_eval_prompt,
    build_layer_prompt,
)
from app.state import (
    LAYER_ORDER,
    PipelineState,
    approve_layer,
    make_initial_state,
    mark_running,
)


def _make_state(prompt: str = "test", project_dir: str = "/tmp/test") -> PipelineState:
    return make_initial_state(prompt, project_dir, {}, "run_test")


FULL_CONTEXT_OUTPUT = {
    "sources": [
        {"type": "file", "path": "src/auth.py", "summary": "Auth module", "verified": True},
        {"type": "file", "path": "src/db.py", "summary": "Database", "verified": True},
    ],
    "gathered_info": "This is a very long verbose working notes section with lots of detail.",
    "distilled_context": "Compressed briefing for downstream.",
    "dependencies": ["pydantic", "click"],
    "gaps": ["No test coverage info"],
    "relevant_history": "Recent refactor in auth module.",
}


class TestSlimContext:
    def test_slim_context_preserves_distilled(self):
        slimmed = _slim_context_for_downstream(FULL_CONTEXT_OUTPUT)
        assert slimmed["distilled_context"] == "Compressed briefing for downstream."

    def test_slim_context_strips_gathered_info(self):
        slimmed = _slim_context_for_downstream(FULL_CONTEXT_OUTPUT)
        assert "gathered_info" not in slimmed

    def test_slim_context_strips_sources(self):
        slimmed = _slim_context_for_downstream(FULL_CONTEXT_OUTPUT)
        assert "sources" not in slimmed
        assert slimmed["source_count"] == 2

    def test_slim_context_handles_missing_fields(self):
        slimmed = _slim_context_for_downstream({})
        assert slimmed["distilled_context"] == ""
        assert slimmed["dependencies"] == []
        assert slimmed["gaps"] == []
        assert slimmed["source_count"] == 0


class TestBuildLayerPromptSlimming:
    def test_build_layer_prompt_slims_context_for_intent(self):
        state = _make_state()
        # Approve prompt
        state = mark_running(state, "prompt")
        state = approve_layer(
            state, "prompt",
            output={"task_description": "test", "complexity": {"level": "low"}},
            eval_verdict=None, usage=None, session_id=None,
        )
        # Approve context with full output
        state = mark_running(state, "context")
        state = approve_layer(
            state, "context",
            output=FULL_CONTEXT_OUTPUT,
            eval_verdict=None, usage=None, session_id=None,
        )
        # Build prompt for intent layer
        prompt_text = build_layer_prompt("intent", state)
        assert "gathered_info" not in prompt_text
        assert "distilled_context" in prompt_text
        assert "source_count" in prompt_text


class TestBuildEvalPromptContext:
    def test_build_eval_prompt_full_context_for_context_eval(self):
        state = _make_state()
        state = mark_running(state, "prompt")
        state = approve_layer(
            state, "prompt",
            output={"task_description": "test"},
            eval_verdict=None, usage=None, session_id=None,
        )
        # Evaluating context layer — should see full context output including gathered_info
        # (Context output is passed as the layer_output arg, not from prior layers)
        prompt_text = build_eval_prompt("context", FULL_CONTEXT_OUTPUT, state)
        assert "gathered_info" in prompt_text

    def test_build_eval_prompt_slims_context_for_intent_eval(self):
        state = _make_state()
        state = mark_running(state, "prompt")
        state = approve_layer(
            state, "prompt",
            output={"task_description": "test"},
            eval_verdict=None, usage=None, session_id=None,
        )
        state = mark_running(state, "context")
        state = approve_layer(
            state, "context",
            output=FULL_CONTEXT_OUTPUT,
            eval_verdict=None, usage=None, session_id=None,
        )
        # Evaluating intent layer — prior context output should be slimmed
        prompt_text = build_eval_prompt("intent", {"refined_goal": "test"}, state)
        assert "gathered_info" not in prompt_text
        assert "distilled_context" in prompt_text


class TestEvalRetryMemory:
    def test_eval_prompt_includes_prior_findings_on_retry(self):
        state = _make_state()
        prompt_text = build_eval_prompt(
            "prompt", {"task": "test"}, state,
            is_retry=True, prior_findings=["hollowness detected"],
        )
        assert "Prior Eval" in prompt_text
        assert "hollowness detected" in prompt_text

    def test_eval_prompt_no_prior_section_on_first_attempt(self):
        state = _make_state()
        prompt_text = build_eval_prompt(
            "prompt", {"task": "test"}, state,
            is_retry=False,
        )
        assert "Prior Eval" not in prompt_text
