"""Session validation after --resume.

When resuming a session for Mode B retries, we must verify that Claude actually
loaded the prior conversation. If the session expired or was corrupted, --resume
might silently create a fresh session.
"""

from __future__ import annotations

from app.state import UsageRecord


def validate_resumed_session(
    result: dict,
    expected_session_id: str,
    previous_usage: UsageRecord | None,
    token_threshold: int = 1000,
    enabled: bool = True,
) -> tuple[bool, str]:
    """Validate that a --resume call actually resumed the expected session.

    Returns (is_valid, reason).

    Checks:
    1. session_id in response matches expected_session_id
    2. num_turns > 1 (multi-turn means prior context exists)
    3. cache_read_input_tokens > 0 (prior context should be cached)

    On failure the orchestrator should fall back to a fresh session with full
    context re-injection.
    """
    if not enabled:
        return True, "Validation disabled"

    actual_session_id = result.get("session_id")
    num_turns = result.get("num_turns", 0)
    cache_read = result.get("usage", {}).get("cache_read_input_tokens", 0)

    if actual_session_id != expected_session_id:
        return False, (
            f"Session ID changed: expected {expected_session_id}, "
            f"got {actual_session_id}"
        )

    if num_turns <= 1:
        return False, f"Expected multi-turn session but num_turns={num_turns}"

    # A resumed session should have significant cache reads from the prior turn.
    # If cache_read is 0 and the previous turn had substantial input tokens,
    # Claude likely started fresh.
    if previous_usage and cache_read == 0 and previous_usage.tokens_in > token_threshold:
        return False, "No cache reads detected — session may not have prior context"

    return True, "Session resumed successfully"
