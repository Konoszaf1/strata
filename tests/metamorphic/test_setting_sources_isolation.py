"""Metamorphic test: only Coherence should load project settings.

Verifies that --setting-sources is configured exclusively for the Coherence
layer, ensuring cognitive isolation for layers 1-4.
"""

import pytest

from app.config import load_config


class TestSettingSourcesIsolation:
    def test_only_coherence_has_setting_sources(self):
        config = load_config()

        for name in ["prompt", "context", "intent", "judgment"]:
            layer = config.get_layer(name)
            assert layer.setting_sources is None, (
                f"Layer {name} should NOT have setting_sources "
                f"(found: {layer.setting_sources})"
            )

        coherence = config.get_layer("coherence")
        assert coherence.setting_sources is not None
        assert "project" in coherence.setting_sources

    def test_setting_sources_includes_user_and_project(self):
        config = load_config()
        coherence = config.get_layer("coherence")
        assert "user" in coherence.setting_sources
        assert "project" in coherence.setting_sources

    def test_override_does_not_leak_setting_sources(self):
        """Even with CLI overrides, non-coherence layers shouldn't get setting_sources."""
        config = load_config(
            cli_overrides={"layers": {"prompt": {"model": "opus"}}}
        )
        prompt = config.get_layer("prompt")
        assert prompt.setting_sources is None
