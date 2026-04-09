"""Tests for AIQA Tier 3 cross-run drift detection."""

import json
import tempfile
from pathlib import Path

import pytest

from app.qa.drift import (
    DriftReport,
    _compute_quality_trend,
    _find_pattern_conflicts,
    _find_recurring_failures,
    _find_recurring_findings,
    analyze_drift,
)


def _make_transcript(
    run_id: str,
    eval_verdicts: list[str] | None = None,
    coherence_output: dict | None = None,
    incomplete: bool = False,
) -> dict:
    """Create a minimal transcript for testing."""
    events = []
    for i, v in enumerate(eval_verdicts or ["pass"]):
        events.append({
            "type": "eval",
            "layer": ["prompt", "context", "intent", "judgment", "coherence"][i % 5],
            "verdict": {"verdict": v, "findings": [], "summary": "test"},
        })

    t = {
        "run_id": run_id,
        "started_at": "2026-03-16T00:00:00Z",
        "finished_at": "2026-03-16T01:00:00Z",
        "events": events,
        "final_layers": {},
    }

    if incomplete:
        t["status"] = "incomplete"

    if coherence_output:
        t["final_layers"]["coherence"] = {"output": coherence_output}

    return t


def _write_transcripts(tmpdir: str, transcripts: list[dict]) -> None:
    """Write transcript files to .stack/transcripts/."""
    tdir = Path(tmpdir) / ".stack" / "transcripts"
    tdir.mkdir(parents=True, exist_ok=True)
    for i, t in enumerate(transcripts):
        (tdir / f"run_{i:03d}.json").write_text(json.dumps(t))


class TestQualityTrend:
    def test_all_pass_is_stable(self):
        transcripts = [
            _make_transcript(f"r{i}", ["pass", "pass"]) for i in range(5)
        ]
        assert _compute_quality_trend(transcripts) == "stable"

    def test_declining_trend(self):
        # First half: all pass, second half: all fail
        transcripts = (
            [_make_transcript(f"r{i}", ["pass", "pass"]) for i in range(3)]
            + [_make_transcript(f"r{i}", ["fail", "fail"]) for i in range(3, 6)]
        )
        assert _compute_quality_trend(transcripts) == "declining"

    def test_improving_trend(self):
        transcripts = (
            [_make_transcript(f"r{i}", ["fail", "fail"]) for i in range(3)]
            + [_make_transcript(f"r{i}", ["pass", "pass"]) for i in range(3, 6)]
        )
        assert _compute_quality_trend(transcripts) == "improving"

    def test_insufficient_data(self):
        transcripts = [_make_transcript("r0", ["pass"])]
        assert _compute_quality_trend(transcripts) == "insufficient_data"


class TestRecurringFailures:
    def test_detects_recurring(self):
        transcripts = [
            _make_transcript(f"r{i}", ["fail"]) for i in range(4)
        ]
        failures = _find_recurring_failures(transcripts)
        assert len(failures) >= 1
        assert "prompt" in failures[0]

    def test_no_recurring_below_threshold(self):
        transcripts = [
            _make_transcript("r0", ["fail"]),
            _make_transcript("r1", ["pass"]),
            _make_transcript("r2", ["pass"]),
            _make_transcript("r3", ["pass"]),
        ]
        failures = _find_recurring_failures(transcripts)
        assert failures == []


class TestPatternConflicts:
    def test_detects_elevated_drift(self):
        transcripts = [
            _make_transcript(
                f"r{i}",
                coherence_output={
                    "consistency_check": {
                        "drift_risk": "high",
                        "principle_violations": [],
                    }
                },
            )
            for i in range(3)
        ]
        conflicts = _find_pattern_conflicts(transcripts)
        assert any("drift_risk" in c for c in conflicts)

    def test_detects_recurring_violations(self):
        transcripts = [
            _make_transcript(
                f"r{i}",
                coherence_output={
                    "consistency_check": {
                        "drift_risk": "low",
                        "principle_violations": ["Inconsistent naming convention"],
                    }
                },
            )
            for i in range(3)
        ]
        conflicts = _find_pattern_conflicts(transcripts)
        assert any("principle violations" in c.lower() for c in conflicts)


class TestAnalyzeDrift:
    def test_insufficient_data(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            report = analyze_drift(tmpdir)
            assert report.quality_trend == "insufficient_data"

    def test_with_transcripts(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            transcripts = [
                _make_transcript(f"r{i}", ["pass", "pass"]) for i in range(5)
            ]
            _write_transcripts(tmpdir, transcripts)
            report = analyze_drift(tmpdir)
            assert report.quality_trend in ("stable", "improving", "declining")
            assert report.run_count == 5

    def test_skips_incomplete(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            transcripts = [
                _make_transcript(f"r{i}", ["pass"], incomplete=True) for i in range(5)
            ]
            _write_transcripts(tmpdir, transcripts)
            report = analyze_drift(tmpdir)
            assert report.quality_trend == "insufficient_data"
            assert report.run_count == 0

    def test_no_transcripts_dir(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            report = analyze_drift(tmpdir)
            assert report.quality_trend == "insufficient_data"


class TestRecurringFindings:
    def _make_transcript_with_findings(
        self, run_id: str, layer: str, findings: list[str]
    ) -> dict:
        """Create a transcript with eval findings for a specific layer."""
        events = [{
            "type": "eval",
            "layer": layer,
            "verdict": {"verdict": "concern", "findings": findings, "summary": "test"},
        }]
        return {
            "run_id": run_id,
            "started_at": "2026-03-16T00:00:00Z",
            "finished_at": "2026-03-16T01:00:00Z",
            "events": events,
            "final_layers": {},
        }

    def test_recurring_findings_detects_repeated_finding(self):
        transcripts = [
            self._make_transcript_with_findings(f"r{i}", "judgment", ["hollowness in degradation_protocol"])
            for i in range(3)
        ]
        results = _find_recurring_findings(transcripts)
        assert len(results) >= 1
        assert "hollowness" in results[0].lower()

    def test_recurring_findings_ignores_one_off(self):
        transcripts = [
            self._make_transcript_with_findings("r0", "judgment", ["one-off finding"]),
        ] + [
            self._make_transcript_with_findings(f"r{i}", "judgment", ["other stuff"])
            for i in range(1, 5)
        ]
        results = _find_recurring_findings(transcripts)
        assert not any("one-off" in r for r in results)

    def test_recurring_findings_deduplicates_within_run(self):
        # Same finding appears twice in one run (from retry) — should count as 1
        transcript = {
            "run_id": "r0",
            "started_at": "2026-03-16T00:00:00Z",
            "finished_at": "2026-03-16T01:00:00Z",
            "events": [
                {"type": "eval", "layer": "judgment",
                 "verdict": {"verdict": "fail", "findings": ["repeated issue"], "summary": "1st"}},
                {"type": "eval", "layer": "judgment",
                 "verdict": {"verdict": "concern", "findings": ["repeated issue"], "summary": "2nd"}},
            ],
            "final_layers": {},
        }
        transcripts = [transcript] + [
            self._make_transcript_with_findings(f"r{i}", "judgment", ["repeated issue"])
            for i in range(1, 3)
        ]
        results = _find_recurring_findings(transcripts)
        # Should find "repeated issue" recurring in 3/3 runs (not 4 due to dedup)
        assert len(results) >= 1
        matching = [r for r in results if "repeated issue" in r]
        assert len(matching) == 1
        assert "3/3" in matching[0]
