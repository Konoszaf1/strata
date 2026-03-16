"""Rate-limit-aware token usage tracking across pipeline runs.

Stores usage in {project_dir}/.stack/usage.json with timestamps.
Estimates remaining hourly capacity based on recent consumption.
"""

from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from pydantic import BaseModel


class UsageEntry(BaseModel):
    timestamp: float
    layer: str
    model: str
    tokens_in: int
    tokens_out: int


# Approximate Max plan budgets (tokens per hour, rough estimates).
# Actual limits vary by server load and plan details.
PLAN_BUDGETS: dict[str, dict[str, int]] = {
    "max_5x": {"input": 2_000_000, "output": 500_000},
    "max_20x": {"input": 8_000_000, "output": 2_000_000},
}


class UsageTracker:
    """Track token usage across pipeline runs for rate limit awareness."""

    def __init__(self, project_dir: str):
        self._file = Path(project_dir) / ".stack" / "usage.json"
        self._entries: list[UsageEntry] = []
        self._load()

    def _load(self) -> None:
        if self._file.is_file():
            try:
                data = json.loads(self._file.read_text())
                self._entries = [UsageEntry(**e) for e in data]
            except (json.JSONDecodeError, KeyError):
                self._entries = []
        self._prune()

    def _prune(self, max_age_seconds: int = 86400) -> None:
        """Remove entries older than max_age_seconds (default 24h)."""
        cutoff = time.time() - max_age_seconds
        self._entries = [e for e in self._entries if e.timestamp >= cutoff]

    def _save(self) -> None:
        self._prune()
        self._file.parent.mkdir(parents=True, exist_ok=True)
        data = [e.model_dump() for e in self._entries]
        self._file.write_text(json.dumps(data, indent=2))

    def record_usage(
        self, layer: str, model: str, tokens_in: int, tokens_out: int
    ) -> None:
        entry = UsageEntry(
            timestamp=time.time(),
            layer=layer,
            model=model,
            tokens_in=tokens_in,
            tokens_out=tokens_out,
        )
        self._entries.append(entry)
        self._save()

    def get_hourly_usage(self) -> dict[str, int]:
        """Sum tokens consumed in the last hour."""
        cutoff = time.time() - 3600
        total_in = 0
        total_out = 0
        for e in self._entries:
            if e.timestamp >= cutoff:
                total_in += e.tokens_in
                total_out += e.tokens_out
        return {"input": total_in, "output": total_out}

    def estimate_remaining_pct(self, plan: str = "max_5x") -> float:
        """Estimate remaining hourly capacity as a fraction (0.0–1.0).

        Returns 1.0 if no usage in the last hour. Returns 0.0 if budget exhausted.
        """
        budget = PLAN_BUDGETS.get(plan, PLAN_BUDGETS["max_5x"])
        hourly = self.get_hourly_usage()

        # Use the tighter constraint (input or output)
        in_pct = 1.0 - (hourly["input"] / budget["input"]) if budget["input"] else 1.0
        out_pct = 1.0 - (hourly["output"] / budget["output"]) if budget["output"] else 1.0

        return max(0.0, min(in_pct, out_pct))

    def pre_flight_check(
        self,
        config_snapshot: dict,
        prompt_length: int,
        plan: str = "max_5x",
    ) -> str | None:
        """Return a warning string if estimated usage is concerning, else None."""
        remaining = self.estimate_remaining_pct(plan)

        if remaining < 0.1:
            return (
                f"WARNING: Only ~{int(remaining * 100)}% of hourly budget remains. "
                "Consider waiting before starting a new pipeline run."
            )
        if remaining < 0.3:
            return (
                f"Note: ~{int((1 - remaining) * 100)}% of hourly budget already used. "
                "A full pipeline run may approach the limit."
            )

        # Rough estimate: each layer + eval = ~5k input tokens on average
        enabled_layers = sum(
            1
            for cfg in config_snapshot.get("layers", {}).values()
            if isinstance(cfg, dict) and cfg.get("enabled", True)
        )
        estimated_calls = enabled_layers * 2  # layer + eval
        estimated_input = (prompt_length + 5000) * estimated_calls
        budget = PLAN_BUDGETS.get(plan, PLAN_BUDGETS["max_5x"])

        if estimated_input > budget["input"] * 0.3:
            return (
                f"Note: This prompt ({prompt_length} chars) across {enabled_layers} layers "
                f"may use ~{int(estimated_input / budget['input'] * 100)}% of hourly input budget."
            )

        return None
