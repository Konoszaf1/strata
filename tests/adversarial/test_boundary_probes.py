"""Adversarial tests for boundary checking.

Probes that attempt to smuggle concepts across layer boundaries.
"""

import pytest

from app.qa.boundary_check import FORBIDDEN_CONCEPTS, check_boundaries


class TestBoundaryProbes:
    def test_prompt_cannot_assess_risk(self):
        output = {
            "task_description": "Refactor auth",
            "scope": "src/auth",
            "notes": "There is a risk of breaking existing sessions",
        }
        violations = check_boundaries("prompt", output, "Refactor auth")
        assert any("risk" in v.lower() and "boundary_violation" in v for v in violations)

    def test_context_cannot_define_goals(self):
        output = {
            "sources": [],
            "gathered_info": "The success criteria should be full test coverage",
        }
        violations = check_boundaries("context", output, "Add tests")
        assert any("success criteria" in v.lower() and "boundary_violation" in v for v in violations)

    def test_intent_cannot_reference_claude_md(self):
        output = {
            "refined_goal": "Follow CLAUDE.md conventions",
            "success_criteria": [],
        }
        violations = check_boundaries("intent", output, "refactor")
        assert any("CLAUDE.md" in v for v in violations)

    def test_judgment_cannot_provide_implementation(self):
        output = {
            "risks": [],
            "final_output": "here is the code: def foo(): pass",
        }
        violations = check_boundaries("judgment", output, "implement foo")
        # "here is the code" matches a forbidden concept
        assert any("boundary_violation" in v for v in violations)

    def test_all_layers_have_forbidden_concepts_except_coherence(self):
        for layer in ["prompt", "context", "intent", "judgment"]:
            assert layer in FORBIDDEN_CONCEPTS
            assert len(FORBIDDEN_CONCEPTS[layer]) > 0

        assert "coherence" not in FORBIDDEN_CONCEPTS

    def test_case_insensitive_detection(self):
        output = {"notes": "We should check PROJECT CONVENTION alignment"}
        violations = check_boundaries("prompt", output, "test")
        assert any("project convention" in v.lower() for v in violations)

    def test_judgment_catches_code_patterns(self):
        output = {"analysis": "def handle_auth(): pass"}
        violations = check_boundaries("judgment", output, "test")
        assert any("boundary_violation" in v for v in violations)
