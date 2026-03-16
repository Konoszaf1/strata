"""Tests for config loading, merging, and boundary checks."""

import json
import tempfile
from pathlib import Path

import pytest

from app.config import PipelineConfig, _deep_merge, load_config
from app.qa.boundary_check import check_boundaries


class TestConfigMerge:
    def test_deep_merge_simple(self):
        base = {"a": 1, "b": {"c": 2, "d": 3}}
        override = {"b": {"c": 99}, "e": 5}
        result = _deep_merge(base, override)
        assert result == {"a": 1, "b": {"c": 99, "d": 3}, "e": 5}

    def test_deep_merge_no_mutation(self):
        base = {"a": {"b": 1}}
        override = {"a": {"c": 2}}
        _deep_merge(base, override)
        assert base == {"a": {"b": 1}}

    def test_load_defaults(self):
        config = load_config()
        assert config.skip_policy in ("never", "next", "recommended", "always")
        assert config.eval_gate in ("human", "auto")

    def test_cli_overrides(self):
        config = load_config(cli_overrides={"skip_policy": "always", "eval_gate": "auto"})
        assert config.skip_policy == "always"
        assert config.eval_gate == "auto"

    def test_coherence_has_setting_sources(self):
        config = load_config()
        coherence = config.get_layer("coherence")
        assert coherence.setting_sources is not None
        assert "project" in coherence.setting_sources

    def test_non_coherence_no_setting_sources(self):
        config = load_config()
        for name in ["prompt", "context", "intent", "judgment"]:
            layer = config.get_layer(name)
            assert layer.setting_sources is None


class TestBoundaryCheck:
    def test_no_violations_on_clean_output(self):
        output = {"task_description": "Add a button", "scope": "src/ui"}
        violations = check_boundaries("prompt", output, "Add a button")
        assert all("boundary_violation" not in v for v in violations)

    def test_detects_boundary_violation(self):
        output = {
            "task_description": "Add a button",
            "risk": "This might break the UI",  # Risk is Judgment's job
        }
        violations = check_boundaries("prompt", output, "Add a button")
        assert any("boundary_violation" in v for v in violations)

    def test_user_originated_not_flagged_as_violation(self):
        # If the user's prompt mentions "risk", the prompt agent echoing it is fine
        output = {"task_description": "Fix this risk in the auth module"}
        violations = check_boundaries("prompt", output, "Fix this risk in auth")
        assert all("user_originated" in v for v in violations)

    def test_coherence_has_no_forbidden_concepts(self):
        output = {
            "final_output": "Here's code following CLAUDE.md project conventions",
            "risk": "handled",
        }
        violations = check_boundaries("coherence", output, "test")
        assert violations == []

    def test_judgment_boundary(self):
        output = {
            "risks": [{"risk": "test"}],
            "final implementation": "some code",  # Coherence's job
        }
        violations = check_boundaries("judgment", output, "test prompt")
        assert any("final implementation" in v for v in violations)
