"""Tests for pipeline output parsing (_parse_agent_output)."""

import pytest

from app.pipeline import _parse_agent_output


class TestParseAgentOutput:
    def test_parse_agent_output_valid_json(self):
        result = _parse_agent_output({"result": '{"task": "test", "status": "ok"}'})
        assert result == {"task": "test", "status": "ok"}
        assert "_parse_error" not in result

    def test_parse_agent_output_markdown_fenced(self):
        result = _parse_agent_output({"result": '```json\n{"task": "test"}\n```'})
        assert result == {"task": "test"}
        assert "_parse_error" not in result

    def test_parse_agent_output_invalid_returns_error(self):
        result = _parse_agent_output({"result": "This is not JSON at all"})
        assert "_parse_error" in result
        assert "_raw" in result

    def test_parse_agent_output_empty_returns_error(self):
        result = _parse_agent_output({"result": ""})
        assert "_parse_error" in result
