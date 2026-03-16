"""Tests for TranscriptWriter — event accumulation, partial writes, and finalization."""

import json
from pathlib import Path

import pytest

from app.qa.transcript import TranscriptWriter
from app.state import make_initial_state


@pytest.fixture
def tmp_project(tmp_path):
    return str(tmp_path)


@pytest.fixture
def writer(tmp_project):
    return TranscriptWriter(tmp_project, "run_test123")


class TestTranscriptWriter:
    def test_log_event_accumulates(self, writer):
        writer.log_event("test", {"key": "value"})
        writer.log_event("test2", {"key2": "value2"})
        assert len(writer._events) == 2
        assert writer._events[0]["type"] == "test"
        assert writer._events[1]["type"] == "test2"

    def test_log_layer_start(self, writer):
        writer.log_layer_start("prompt", 1)
        assert len(writer._events) == 1
        assert writer._events[0]["type"] == "layer_start"
        assert writer._events[0]["layer"] == "prompt"

    def test_log_auto_approve(self, writer):
        writer.log_auto_approve("context")
        assert writer._events[0]["type"] == "auto_approve"
        assert writer._events[0]["layer"] == "context"

    def test_write_partial_creates_incomplete_transcript(self, writer, tmp_project):
        writer.log_event("test", {"key": "value"})
        path = writer.write_partial()

        assert path.is_file()
        data = json.loads(path.read_text())
        assert data["status"] == "incomplete"
        assert data["run_id"] == "run_test123"
        assert len(data["events"]) == 1

    def test_finalize_creates_complete_transcript(self, writer, tmp_project):
        writer.log_layer_start("prompt", 1)
        state = make_initial_state(
            prompt="test",
            project_dir=tmp_project,
            config_snapshot={},
            run_id="run_test123",
        )
        path = writer.finalize(state)

        assert path.is_file()
        data = json.loads(path.read_text())
        assert "status" not in data  # finalize doesn't set status
        assert data["run_id"] == "run_test123"
        assert data["original_prompt"] == "test"
        assert len(data["events"]) == 1

    def test_transcript_dir_created(self, tmp_project):
        writer = TranscriptWriter(tmp_project, "run_xyz")
        transcript_dir = Path(tmp_project) / ".stack" / "transcripts"
        assert transcript_dir.is_dir()
