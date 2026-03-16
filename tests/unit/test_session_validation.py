"""Tests for session resume validation."""

import pytest

from app.agents.validation import validate_resumed_session
from app.state import UsageRecord


@pytest.fixture
def prev_usage():
    return UsageRecord(
        tokens_in=5000,
        tokens_out=2000,
        model="sonnet",
        latency_ms=3000,
        session_id="ses_expected",
    )


class TestSessionValidation:
    def test_valid_resume(self, prev_usage):
        result = {
            "session_id": "ses_expected",
            "num_turns": 3,
            "usage": {"cache_read_input_tokens": 4500},
        }
        is_valid, reason = validate_resumed_session(result, "ses_expected", prev_usage)
        assert is_valid is True
        assert "success" in reason.lower()

    def test_session_id_mismatch(self, prev_usage):
        result = {
            "session_id": "ses_different",
            "num_turns": 3,
            "usage": {"cache_read_input_tokens": 4500},
        }
        is_valid, reason = validate_resumed_session(result, "ses_expected", prev_usage)
        assert is_valid is False
        assert "changed" in reason.lower()

    def test_single_turn_suspicious(self, prev_usage):
        result = {
            "session_id": "ses_expected",
            "num_turns": 1,
            "usage": {"cache_read_input_tokens": 4500},
        }
        is_valid, reason = validate_resumed_session(result, "ses_expected", prev_usage)
        assert is_valid is False
        assert "num_turns" in reason

    def test_no_cache_reads_suspicious(self, prev_usage):
        result = {
            "session_id": "ses_expected",
            "num_turns": 3,
            "usage": {"cache_read_input_tokens": 0},
        }
        is_valid, reason = validate_resumed_session(result, "ses_expected", prev_usage)
        assert is_valid is False
        assert "cache" in reason.lower()

    def test_no_cache_reads_ok_for_small_session(self):
        small_usage = UsageRecord(
            tokens_in=500,
            tokens_out=200,
            model="haiku",
            latency_ms=500,
            session_id="ses_expected",
        )
        result = {
            "session_id": "ses_expected",
            "num_turns": 2,
            "usage": {"cache_read_input_tokens": 0},
        }
        is_valid, reason = validate_resumed_session(result, "ses_expected", small_usage)
        assert is_valid is True

    def test_no_previous_usage(self):
        result = {
            "session_id": "ses_expected",
            "num_turns": 2,
            "usage": {"cache_read_input_tokens": 0},
        }
        is_valid, reason = validate_resumed_session(result, "ses_expected", None)
        assert is_valid is True

    def test_custom_token_threshold(self, prev_usage):
        """With a higher threshold, cache check is skipped for moderate usage."""
        result = {
            "session_id": "ses_expected",
            "num_turns": 3,
            "usage": {"cache_read_input_tokens": 0},
        }
        # prev_usage has tokens_in=5000. With threshold=10000 the check is skipped.
        is_valid, reason = validate_resumed_session(
            result, "ses_expected", prev_usage, token_threshold=10000
        )
        assert is_valid is True

    def test_validation_disabled(self, prev_usage):
        """When disabled, validation always passes."""
        result = {
            "session_id": "ses_WRONG",
            "num_turns": 0,
            "usage": {"cache_read_input_tokens": 0},
        }
        is_valid, reason = validate_resumed_session(
            result, "ses_expected", prev_usage, enabled=False
        )
        assert is_valid is True
        assert "disabled" in reason.lower()
