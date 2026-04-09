"""Claude CLI subprocess wrapper.

All layer and eval agents run as `claude -p` subprocesses.
Uses --append-system-prompt-file to preserve Claude Code's built-in capabilities.
Prompts are piped via stdin to avoid shell escaping and length limits.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from pathlib import Path
from typing import Any, Callable, Awaitable

from app.state import UsageRecord

logger = logging.getLogger(__name__)


class ClaudeCliError(Exception):
    def __init__(self, stderr: str, returncode: int):
        self.stderr = stderr
        self.returncode = returncode
        super().__init__(f"claude -p failed (rc={returncode}): {stderr[:500]}")


class RateLimitError(ClaudeCliError):
    """Raised when claude -p returns a rate limit error."""

    pass


class LayerCancelled(Exception):
    """Raised when the user interrupts a running layer (Ctrl+C)."""

    pass


def _parse_usage(raw: dict) -> UsageRecord | None:
    """Extract UsageRecord from a claude --output-format json response."""
    usage = raw.get("usage")
    if not usage:
        return None
    return UsageRecord(
        tokens_in=usage.get("input_tokens", 0),
        tokens_out=usage.get("output_tokens", 0),
        cache_read_tokens=usage.get("cache_read_input_tokens", 0),
        cache_creation_tokens=usage.get("cache_creation_input_tokens", 0),
        model=raw.get("model", "unknown"),
        latency_ms=raw.get("_latency_ms", raw.get("duration_ms", 0)),
        session_id=raw.get("session_id", ""),
    )


async def run_claude(
    prompt: str,
    append_system_prompt_file: Path,
    model: str = "sonnet",
    max_turns: int = 5,
    allowed_tools: list[str] | None = None,
    project_dir: str = ".",
    resume_session: str | None = None,
    setting_sources: list[str] | None = None,
    timeout_seconds: int = 600,
    retry_on_rate_limit: bool = True,
    max_retries: int = 3,
    on_rate_limit_wait: Callable[[int, int], Awaitable[None]] | None = None,
) -> dict[str, Any]:
    """Run a single claude -p subprocess call.

    CRITICAL: Uses --append-system-prompt-file (NOT --system-prompt).
    Prompt is piped via stdin to avoid shell escaping issues.

    Parameters
    ----------
    prompt:
        The prompt text, piped via stdin.
    append_system_prompt_file:
        Path to the agent .md file appended to Claude's default system prompt.
    model:
        Model to use (haiku, sonnet, opus).
    max_turns:
        Maximum agentic turns.
    allowed_tools:
        Restrict available tools.
    project_dir:
        Working directory for the subprocess (the target project).
    resume_session:
        Session ID to resume (Mode B retries).
    setting_sources:
        --setting-sources flag. Only used for Coherence agent.
    timeout_seconds:
        Kill subprocess after this many seconds.
    retry_on_rate_limit:
        Whether to auto-retry on rate limit errors.
    max_retries:
        Maximum retry attempts for rate limit errors.
    on_rate_limit_wait:
        Callback(wait_seconds, attempt) for UI countdown display.

    Returns
    -------
    Parsed JSON response from claude CLI, including session_id, result, usage.
    """
    cmd = ["claude", "-p"]
    cmd += ["--output-format", "json"]
    cmd += ["--model", model]
    cmd += ["--max-turns", str(max_turns)]

    # APPEND system prompt — preserves Claude Code's built-in capabilities
    cmd += ["--append-system-prompt-file", str(append_system_prompt_file)]

    if allowed_tools:
        cmd += ["--allowedTools", ",".join(allowed_tools)]

    if resume_session:
        cmd += ["--resume", resume_session]

    if setting_sources:
        cmd += ["--setting-sources", ",".join(setting_sources)]

    json_failures = 0
    for attempt in range(max_retries + 1):
        start_time = time.monotonic()

        logger.debug("Running: %s (attempt %d)", " ".join(cmd), attempt + 1)

        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=project_dir,
        )

        try:
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(input=prompt.encode("utf-8")),
                timeout=timeout_seconds,
            )
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()
            raise ClaudeCliError(f"Timed out after {timeout_seconds}s", -1)
        except (KeyboardInterrupt, asyncio.CancelledError):
            # User pressed Ctrl+C — kill subprocess, raise cancellation
            logger.info("Layer cancelled by user, killing subprocess")
            proc.kill()
            await proc.wait()
            raise LayerCancelled()

        elapsed_ms = int((time.monotonic() - start_time) * 1000)

        if proc.returncode != 0:
            stderr_text = stderr.decode(errors="replace")
            # Detect rate limit errors
            if "rate limit" in stderr_text.lower() or "throttl" in stderr_text.lower():
                if retry_on_rate_limit and attempt < max_retries:
                    wait = min(30 * (2 ** attempt), 120)  # 30s, 60s, 120s max
                    logger.warning(
                        "Rate limited (attempt %d/%d). Waiting %ds...",
                        attempt + 1,
                        max_retries,
                        wait,
                    )
                    if on_rate_limit_wait:
                        await on_rate_limit_wait(wait, attempt + 1)
                    await asyncio.sleep(wait)
                    continue
                raise RateLimitError(stderr_text, proc.returncode)
            raise ClaudeCliError(stderr_text, proc.returncode)

        try:
            result = json.loads(stdout.decode("utf-8"))
        except json.JSONDecodeError as exc:
            json_failures += 1
            if json_failures <= 1 and attempt < max_retries:
                logger.warning("JSON parse failed (attempt %d), retrying: %s", attempt + 1, exc)
                continue
            raw_out = stdout.decode(errors="replace")[:2000]
            raise ClaudeCliError(
                f"Failed to parse JSON from claude output: {exc}\nRaw: {raw_out}",
                0,
            )

        result["_latency_ms"] = elapsed_ms
        return result

    raise ClaudeCliError("Max retries exceeded", -1)


async def check_claude_cli() -> tuple[bool, str]:
    """Verify claude CLI is installed and responsive.

    Returns (ok, message).
    """
    try:
        proc = await asyncio.create_subprocess_exec(
            "claude", "--version",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=10)
        if proc.returncode == 0:
            version = stdout.decode().strip()
            return True, f"Claude CLI found: {version}"
        return False, f"claude --version failed: {stderr.decode().strip()}"
    except FileNotFoundError:
        return False, (
            "Claude Code CLI not found on PATH.\n"
            "Install: npm install -g @anthropic-ai/claude-code\n"
            "Then authenticate: claude"
        )
    except asyncio.TimeoutError:
        return False, "claude --version timed out"
