"""Pipeline configuration with layered merge (defaults → user → project → CLI)."""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

KNOWN_LAYER_NAMES = {"prompt", "context", "intent", "judgment", "coherence"}


class LayerConfig(BaseModel):
    enabled: bool = True
    model: str = "sonnet"
    max_turns: int = 5
    allowed_tools: list[str] = Field(default_factory=lambda: ["Read", "Glob"])
    setting_sources: list[str] | None = None


class EvalConfig(BaseModel):
    model: str = "sonnet"
    max_turns: int = 2
    allowed_tools: list[str] = Field(default_factory=lambda: ["Read"])


class PipelineConfig(BaseModel):
    skip_policy: Literal["never", "next", "recommended", "always"] = "recommended"
    eval_gate: Literal["human", "auto"] = "human"
    max_retries_per_layer: int = 3
    plan: Literal["max_5x", "max_20x"] = "max_5x"
    session_resume_token_threshold: int = 1000
    session_resume_validation: bool = True
    layers: dict[str, LayerConfig] = Field(default_factory=dict)
    eval: EvalConfig = Field(default_factory=EvalConfig)

    def get_layer(self, name: str) -> LayerConfig:
        return self.layers.get(name, LayerConfig())


def _harness_dir() -> Path:
    """Resolve the shipped harness directory inside this package."""
    return Path(__file__).resolve().parent / "harness"


def _deep_merge(base: dict, override: dict) -> dict:
    """Recursively merge override into base, returning a new dict."""
    merged = dict(base)
    for key, val in override.items():
        if key in merged and isinstance(merged[key], dict) and isinstance(val, dict):
            merged[key] = _deep_merge(merged[key], val)
        else:
            merged[key] = val
    return merged


def _load_yaml(path: Path) -> dict:
    if not path.is_file():
        return {}
    with open(path) as f:
        data = yaml.safe_load(f) or {}
    return data.get("pipeline", data)


def load_config(
    project_dir: str | None = None,
    cli_overrides: dict | None = None,
    harness_override: str | None = None,
) -> PipelineConfig:
    """Load config with merge order: harness defaults → user → project → CLI.

    Parameters
    ----------
    project_dir:
        Absolute path to the project (cwd where user invoked the app).
    cli_overrides:
        Dict of overrides from CLI flags.
    harness_override:
        Optional path to a custom harness directory.
    """
    harness = Path(harness_override) if harness_override else _harness_dir()

    # 1. Shipped defaults
    raw = _load_yaml(harness / "config.yaml")

    # 2. User-global override
    user_cfg = Path.home() / ".stack" / "config.yaml"
    raw = _deep_merge(raw, _load_yaml(user_cfg))

    # 3. Project-level override
    if project_dir:
        project_cfg = Path(project_dir) / ".stack" / "config.yaml"
        raw = _deep_merge(raw, _load_yaml(project_cfg))

    # 4. CLI flags
    if cli_overrides:
        raw = _deep_merge(raw, cli_overrides)

    # Parse layer configs
    layers_raw = raw.pop("layers", {})
    eval_raw = raw.pop("eval", {})

    layers = {}
    for name, cfg in layers_raw.items():
        if isinstance(cfg, dict):
            if name not in KNOWN_LAYER_NAMES:
                logger.warning("Unknown layer %r in config (known: %s) — ignoring", name, ", ".join(sorted(KNOWN_LAYER_NAMES)))
                continue
            layers[name] = LayerConfig(**cfg)

    eval_cfg = EvalConfig(**eval_raw) if eval_raw else EvalConfig()

    return PipelineConfig(layers=layers, eval=eval_cfg, **raw)


def resolve_harness_dir(override: str | None = None) -> Path:
    """Return the effective harness directory."""
    if override:
        p = Path(override)
        if not p.is_dir():
            raise FileNotFoundError(f"Custom harness directory not found: {p}")
        return p
    return _harness_dir()


def ensure_project_dirs(project_dir: str) -> None:
    """Create .stack/ subdirectories in the project if needed."""
    stack_dir = Path(project_dir) / ".stack"
    (stack_dir / "transcripts").mkdir(parents=True, exist_ok=True)
