"""Metamorphic test: upgrading a layer's model should not degrade output quality.

This is a design-level test — it verifies the configuration allows model upgrades
and that the pipeline structure supports comparative evaluation. Actual LLM output
comparison requires live calls and is marked as integration.
"""

import pytest

from app.config import LayerConfig, PipelineConfig, load_config


class TestModelUpgradeConfig:
    """Verify that config supports model tiers and upgrade paths."""

    TIER_ORDER = {"haiku": 0, "sonnet": 1, "opus": 2}

    def test_default_models_are_tiered(self):
        """Higher layers should use equal or higher-tier models."""
        config = load_config()
        layers = ["prompt", "context", "intent", "judgment", "coherence"]
        models = [config.get_layer(name).model for name in layers]

        # All five layers should be non-decreasing: haiku <= sonnet <= opus <= opus <= opus
        for i in range(len(layers) - 1):
            tier_curr = self.TIER_ORDER.get(models[i], -1)
            tier_next = self.TIER_ORDER.get(models[i + 1], -1)
            assert tier_curr <= tier_next, (
                f"Model tier should be non-decreasing from {layers[i]} ({models[i]}) "
                f"to {layers[i+1]} ({models[i+1]})"
            )

    def test_model_override_works(self):
        """CLI override to upgrade a layer's model should apply."""
        config = load_config(
            cli_overrides={"layers": {"prompt": {"model": "opus"}}}
        )
        assert config.get_layer("prompt").model == "opus"

    def test_all_valid_models(self):
        """All configured models should be valid Claude model identifiers."""
        valid = {"haiku", "sonnet", "opus"}
        config = load_config()
        for name in ["prompt", "context", "intent", "judgment", "coherence"]:
            model = config.get_layer(name).model
            assert model in valid, f"Layer {name} has invalid model: {model}"
