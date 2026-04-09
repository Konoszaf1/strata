"""AIQA Tier 3 — Cross-run quality drift detection.

Reads previous transcripts to detect:
- Quality score trends (are eval scores declining?)
- Recurring failures (same layer failing repeatedly across runs?)
- Pattern instability (coherence flagging different styles each run?)
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

logger = logging.getLogger(__name__)


@dataclass
class DriftReport:
    """Summary of quality trends across recent runs."""

    quality_trend: Literal["improving", "stable", "declining", "insufficient_data"]
    recurring_failures: list[str] = field(default_factory=list)
    pattern_conflicts: list[str] = field(default_factory=list)
    recurring_findings: list[str] = field(default_factory=list)
    run_count: int = 0
    recommendation: str = ""


def analyze_drift(project_dir: str, window: int = 10) -> DriftReport:
    """Analyze last N transcripts for quality drift.

    Parameters
    ----------
    project_dir:
        Path to the project directory containing .stack/transcripts/.
    window:
        Number of recent transcripts to analyze.
    """
    transcripts = _load_recent_transcripts(project_dir, window)

    if len(transcripts) < 3:
        return DriftReport(
            quality_trend="insufficient_data",
            run_count=len(transcripts),
            recommendation="Not enough run history for drift analysis (need >= 3 runs).",
        )

    recurring = _find_recurring_failures(transcripts)
    patterns = _find_pattern_conflicts(transcripts)
    findings = _find_recurring_findings(transcripts)
    trend = _compute_quality_trend(transcripts)

    parts = []
    if trend == "declining":
        parts.append("Quality scores are declining across recent runs.")
    if recurring:
        parts.append(f"Recurring failures: {'; '.join(recurring[:3])}.")
    if patterns:
        parts.append(f"Pattern conflicts: {'; '.join(patterns[:3])}.")
    if findings:
        parts.append(f"Recurring findings: {'; '.join(findings[:3])}.")
    if not parts:
        parts.append("No significant drift detected.")

    return DriftReport(
        quality_trend=trend,
        recurring_failures=recurring,
        pattern_conflicts=patterns,
        recurring_findings=findings,
        run_count=len(transcripts),
        recommendation=" ".join(parts),
    )


def _load_recent_transcripts(project_dir: str, window: int) -> list[dict]:
    """Load the most recent transcript files."""
    transcript_dir = Path(project_dir) / ".stack" / "transcripts"
    if not transcript_dir.is_dir():
        return []

    files = sorted(transcript_dir.glob("*.json"), reverse=True)[:window]
    transcripts = []
    for f in files:
        try:
            data = json.loads(f.read_text())
            if data.get("status") != "incomplete":
                transcripts.append(data)
        except (json.JSONDecodeError, OSError):
            continue

    return transcripts


def _find_recurring_failures(transcripts: list[dict]) -> list[str]:
    """Find layers that fail repeatedly across runs."""
    failure_counts: dict[str, int] = {}

    for t in transcripts:
        events = t.get("events", [])
        for event in events:
            if event.get("type") == "eval":
                verdict = event.get("verdict", {})
                if isinstance(verdict, dict) and verdict.get("verdict") == "fail":
                    layer = event.get("layer", "unknown")
                    failure_counts[layer] = failure_counts.get(layer, 0) + 1

    # Flag layers that fail in >= 50% of runs
    threshold = max(2, len(transcripts) // 2)
    return [
        f"{layer}: failed in {count}/{len(transcripts)} runs"
        for layer, count in sorted(failure_counts.items(), key=lambda x: -x[1])
        if count >= threshold
    ]


def _find_recurring_findings(transcripts: list[dict]) -> list[str]:
    """Find specific eval findings that recur across runs.

    Operates at the finding level, not the verdict level.
    Catches patterns like 'hollowness warning in judgment appears in 7/10 runs'
    even when the overall verdict is pass or concern.
    """
    finding_counts: dict[tuple[str, str], int] = {}

    for t in transcripts:
        # Track unique findings per run to avoid double-counting retries
        run_findings: set[tuple[str, str]] = set()
        for event in t.get("events", []):
            if event.get("type") == "eval":
                layer = event.get("layer", "unknown")
                verdict = event.get("verdict", {})
                if isinstance(verdict, dict):
                    for finding in verdict.get("findings", []):
                        if isinstance(finding, str):
                            # Normalize: lowercase, truncate to first 80 chars
                            key = (layer, finding.lower()[:80])
                            run_findings.add(key)
        for key in run_findings:
            finding_counts[key] = finding_counts.get(key, 0) + 1

    # Flag findings that appear in >= 40% of runs
    threshold = max(2, len(transcripts) * 2 // 5)
    results = []
    for (layer, finding), count in sorted(finding_counts.items(), key=lambda x: -x[1]):
        if count >= threshold:
            results.append(f"{layer}: '{finding}' (in {count}/{len(transcripts)} runs)")
    return results[:5]  # Cap at 5 most common


def _find_pattern_conflicts(transcripts: list[dict]) -> list[str]:
    """Detect inconsistent patterns across coherence layer outputs."""
    drift_risks: list[str] = []
    principle_violations: list[str] = []

    for t in transcripts:
        final_layers = t.get("final_layers", {})
        coherence = final_layers.get("coherence")
        if not coherence or not isinstance(coherence, dict):
            continue
        output = coherence.get("output", {})
        if not isinstance(output, dict):
            continue

        cc = output.get("consistency_check", {})
        if isinstance(cc, dict):
            dr = cc.get("drift_risk", "")
            if dr in ("medium", "high"):
                drift_risks.append(dr)
            violations = cc.get("principle_violations", [])
            if isinstance(violations, list):
                principle_violations.extend(violations)

    conflicts = []
    if len(drift_risks) >= 2:
        conflicts.append(
            f"Elevated drift_risk in {len(drift_risks)}/{len(transcripts)} recent runs"
        )
    if len(principle_violations) >= 2:
        # Deduplicate similar violations
        unique = list(set(principle_violations))[:5]
        conflicts.append(
            f"Recurring principle violations: {'; '.join(unique)}"
        )

    return conflicts


def _compute_quality_trend(transcripts: list[dict]) -> str:
    """Compute overall quality trend from eval verdicts."""
    # Score each run: pass=2, concern=1, fail=0
    VERDICT_SCORES = {"pass": 2, "concern": 1, "fail": 0}
    run_scores: list[float] = []

    for t in transcripts:
        events = t.get("events", [])
        scores = []
        for event in events:
            if event.get("type") == "eval":
                verdict = event.get("verdict", {})
                if isinstance(verdict, dict):
                    v = verdict.get("verdict", "concern")
                    scores.append(VERDICT_SCORES.get(v, 1))
        if scores:
            run_scores.append(sum(scores) / len(scores))

    if len(run_scores) < 3:
        return "insufficient_data"

    # Compare first half vs second half
    mid = len(run_scores) // 2
    first_half = sum(run_scores[:mid]) / max(mid, 1)
    second_half = sum(run_scores[mid:]) / max(len(run_scores) - mid, 1)

    delta = second_half - first_half
    if delta > 0.3:
        return "improving"
    elif delta < -0.3:
        return "declining"
    return "stable"
