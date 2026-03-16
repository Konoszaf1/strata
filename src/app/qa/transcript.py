"""Full pipeline run logging — writes transcripts to .stack/transcripts/.

Captures: config, state transitions, agent prompts/responses, eval verdicts,
user decisions, session IDs, per-layer usage, rate limit status.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.state import PipelineState


class TranscriptWriter:
    """Accumulates events during a pipeline run and writes the transcript."""

    def __init__(self, project_dir: str, run_id: str):
        self._dir = Path(project_dir) / ".stack" / "transcripts"
        self._dir.mkdir(parents=True, exist_ok=True)
        self._file = self._dir / f"{run_id}.json"
        self._run_id = run_id
        self._events: list[dict[str, Any]] = []
        self._started = datetime.now(timezone.utc).isoformat()

    def log_event(self, event_type: str, data: dict[str, Any]) -> None:
        self._events.append({
            "type": event_type,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            **data,
        })

    def log_layer_start(self, layer: str, attempt: int) -> None:
        self.log_event("layer_start", {"layer": layer, "attempt": attempt})

    def log_layer_result(
        self, layer: str, output: dict, session_id: str | None, usage: dict | None
    ) -> None:
        self.log_event("layer_result", {
            "layer": layer,
            "output": output,
            "session_id": session_id,
            "usage": usage,
        })

    def log_eval(self, layer: str, verdict: dict) -> None:
        self.log_event("eval", {"layer": layer, "verdict": verdict})

    def log_decision(self, layer: str, action: str, feedback: str | None = None) -> None:
        self.log_event("decision", {
            "layer": layer,
            "action": action,
            "feedback": feedback,
        })

    def log_auto_approve(self, layer: str) -> None:
        self.log_event("auto_approve", {"layer": layer})

    def log_error(self, layer: str, error: str) -> None:
        self.log_event("error", {"layer": layer, "error": error})

    def finalize(self, final_state: PipelineState) -> Path:
        """Write the transcript file and return its path."""
        transcript = {
            "run_id": self._run_id,
            "started_at": self._started,
            "finished_at": datetime.now(timezone.utc).isoformat(),
            "original_prompt": final_state.original_prompt,
            "project_dir": final_state.project_dir,
            "config": final_state.config_snapshot,
            "sessions": {k: v for k, v in final_state.sessions.items() if v},
            "final_layers": {
                name: lr.model_dump(mode="json") if lr else None
                for name, lr in final_state.layers.items()
            },
            "events": self._events,
        }
        self._file.write_text(json.dumps(transcript, indent=2, default=str))
        return self._file

    def write_partial(self) -> Path:
        """Write an incomplete transcript (e.g. on crash or interrupt)."""
        transcript = {
            "run_id": self._run_id,
            "started_at": self._started,
            "finished_at": datetime.now(timezone.utc).isoformat(),
            "status": "incomplete",
            "events": self._events,
        }
        self._file.write_text(json.dumps(transcript, indent=2, default=str))
        return self._file
