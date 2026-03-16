"""Tests for AIQA Tier 1 structural validators."""

import tempfile
from pathlib import Path

import pytest

from app.qa.validators import (
    QAFinding,
    _check_hollowness,
    _check_layer_specific,
    _check_range_constraints,
    _check_referential_integrity,
    validate_layer_output,
)
from app.state import (
    LAYER_ORDER,
    LayerResult,
    PipelineState,
    make_initial_state,
    approve_layer,
    mark_running,
    UsageRecord,
    EvalVerdict,
)


def _make_state(prompt: str = "test", project_dir: str = "/tmp/test") -> PipelineState:
    return make_initial_state(prompt, project_dir, {}, "run_test")


class TestHollowness:
    def test_detects_na(self):
        findings = _check_hollowness("prompt", {"scope": "N/A"})
        assert any(f.check == "hollowness" for f in findings)

    def test_detects_tbd(self):
        findings = _check_hollowness("intent", {"refined_goal": "TBD"})
        assert any(f.check == "hollowness" for f in findings)

    def test_detects_standard_apply(self):
        findings = _check_hollowness("judgment", {"edge_cases": "Standard practices apply"})
        assert any(f.check == "hollowness" for f in findings)

    def test_detects_short_labels(self):
        findings = _check_hollowness("intent", {"constraints": ["perf", "security", "ux"]})
        assert any(f.check == "hollowness" and "short labels" in f.detail for f in findings)

    def test_accepts_substantive_content(self):
        findings = _check_hollowness("intent", {
            "refined_goal": "Refactor the auth module to use async/await for all database calls",
            "constraints": [
                "Must preserve backward compatibility with existing API consumers",
                "Database connection pool must not exceed 10 concurrent connections",
            ],
        })
        assert not any(f.check == "hollowness" for f in findings)

    def test_ignores_internal_fields(self):
        findings = _check_hollowness("prompt", {"_raw": "N/A", "_parse_error": "test"})
        assert findings == []


class TestReferentialIntegrity:
    def test_missing_file_flagged(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            output = {
                "sources": [
                    {"type": "file", "path": "nonexistent.py", "summary": "test", "verified": True}
                ]
            }
            findings = _check_referential_integrity("context", output, tmpdir)
            assert any(f.check == "referential_integrity" and f.severity == "failure" for f in findings)

    def test_existing_file_passes(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            (Path(tmpdir) / "exists.py").write_text("# real file")
            output = {
                "sources": [
                    {"type": "file", "path": "exists.py", "summary": "test", "verified": True}
                ]
            }
            findings = _check_referential_integrity("context", output, tmpdir)
            assert not any(f.severity == "failure" for f in findings)

    def test_unverified_missing_is_info_not_failure(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            output = {
                "sources": [
                    {"type": "file", "path": "maybe.py", "summary": "test", "verified": False}
                ]
            }
            findings = _check_referential_integrity("context", output, tmpdir)
            assert all(f.severity == "info" for f in findings)

    def test_only_runs_for_context(self):
        findings = _check_referential_integrity("prompt", {"sources": []}, "/tmp")
        assert findings == []

    def test_git_sources_skipped(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            output = {
                "sources": [
                    {"type": "git", "path": "HEAD~3", "summary": "test", "verified": True}
                ]
            }
            findings = _check_referential_integrity("context", output, tmpdir)
            assert findings == []


class TestRangeConstraints:
    def test_complexity_layer_mismatch(self):
        state = _make_state()
        output = {
            "complexity": {
                "level": "high",
                "reasoning": "complex",
                "recommended_layers": ["prompt"],  # Only 1 for high complexity
            }
        }
        findings = _check_range_constraints("prompt", output, state)
        assert any(f.check == "range_constraint" for f in findings)

    def test_uniform_severity_flagged(self):
        state = _make_state()
        output = {
            "risks": [
                {"risk": "a", "severity": "low", "mitigation": "x", "detectable": True},
                {"risk": "b", "severity": "low", "mitigation": "y", "detectable": True},
                {"risk": "c", "severity": "low", "mitigation": "z", "detectable": True},
            ]
        }
        findings = _check_range_constraints("judgment", output, state)
        assert any("identical severity" in f.detail for f in findings)


class TestLayerSpecific:
    def _state_with_prompt(self, complexity: str) -> PipelineState:
        state = _make_state()
        state = mark_running(state, "prompt")
        return approve_layer(
            state, "prompt",
            output={"complexity": {"level": complexity}},
            eval_verdict=None, usage=None, session_id=None,
        )

    def test_medium_task_needs_ambiguities(self):
        state = self._state_with_prompt("medium")
        output = {"ambiguities": []}
        # Check prompt layer against its own state
        # We need to put the complexity in the output for prompt layer checks
        findings = _check_layer_specific("prompt", {"ambiguities": [], "complexity": {"level": "medium"}}, state)
        assert any(f.check == "completeness" for f in findings)

    def test_intent_needs_tradeoffs_for_medium(self):
        state = self._state_with_prompt("medium")
        output = {"tradeoffs": [], "decision_boundaries": [{"category": "test"}], "priority_order": []}
        findings = _check_layer_specific("intent", output, state)
        assert any("tradeoffs" in f.detail for f in findings)

    def test_intent_needs_decision_boundaries(self):
        state = self._state_with_prompt("low")
        output = {"tradeoffs": [], "decision_boundaries": [], "priority_order": []}
        findings = _check_layer_specific("intent", output, state)
        assert any("decision boundaries" in f.detail for f in findings)

    def test_judgment_needs_unknowns(self):
        state = self._state_with_prompt("medium")
        output = {"confidence_boundaries": {"unknowns": []}}
        findings = _check_layer_specific("judgment", output, state)
        assert any("unknowns" in f.detail for f in findings)

    def test_judgment_needs_degradation_for_high(self):
        state = self._state_with_prompt("high")
        output = {"confidence_boundaries": {"unknowns": ["something"]}, "degradation_protocol": None}
        findings = _check_layer_specific("judgment", output, state)
        assert any("degradation_protocol" in f.detail for f in findings)

    def test_coherence_needs_judgment_responses(self):
        state = self._state_with_prompt("medium")
        # Advance through intent, then judgment
        state = mark_running(state, "context")
        state = approve_layer(state, "context", output={}, eval_verdict=None, usage=None, session_id=None)
        state = mark_running(state, "intent")
        state = approve_layer(state, "intent", output={}, eval_verdict=None, usage=None, session_id=None)
        state = mark_running(state, "judgment")
        state = approve_layer(
            state, "judgment",
            output={"risks": [{"risk": "something", "severity": "medium"}]},
            eval_verdict=None, usage=None, session_id=None,
        )
        output = {"judgment_responses": [], "consistency_check": {"prior_patterns": "x", "style_coherence": "y"}}
        findings = _check_layer_specific("coherence", output, state)
        assert any("judgment_responses" in f.detail for f in findings)

    def test_context_compression_check(self):
        state = self._state_with_prompt("medium")
        output = {
            "gathered_info": "This is a long gathered info section",
            "distilled_context": "This is a longer distilled context that is not actually compressed at all and keeps going",
        }
        findings = _check_layer_specific("context", output, state)
        assert any("compression" in f.detail for f in findings)


class TestValidateLayerOutput:
    def test_returns_sorted_by_severity(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            state = _make_state(project_dir=tmpdir)
            output = {
                "sources": [
                    {"type": "file", "path": "missing.py", "summary": "test", "verified": True}
                ],
                "gathered_info": "short",
                "distilled_context": "N/A",
                "gaps": [],
            }
            findings = validate_layer_output("context", output, state, tmpdir)
            severities = [f.severity for f in findings]
            # Failures should come before warnings
            if "failure" in severities and "warning" in severities:
                assert severities.index("failure") < severities.index("warning")
