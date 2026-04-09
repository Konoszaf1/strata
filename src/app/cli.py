"""Click CLI entry point for the AI-Human Engineering Stack."""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import sys
import uuid
from pathlib import Path

import click
from rich.console import Console
from rich.panel import Panel

from app.bootstrap import needs_bootstrap, run_bootstrap
from app.config import PipelineConfig, ensure_project_dirs, load_config
from app.pipeline import run_pipeline
from app.qa.transcript import TranscriptWriter
from app.qa.usage_tracker import UsageTracker
from app.state import LAYER_ORDER
from app.ui.checkpoint import (
    prompt_interrupt_decision,
    prompt_layer_steering,
    prompt_user_decision,
    render_auto_approved,
    render_eval_start,
    render_layer_start,
)
from app.ui.themes import STACK_THEME

console = Console(theme=STACK_THEME)


def _check_prerequisites() -> list[str]:
    """Verify prerequisites are met. Returns list of error messages."""
    errors: list[str] = []

    # Python version
    if sys.version_info < (3, 12):
        errors.append(
            f"Python 3.12+ required (found {sys.version_info.major}.{sys.version_info.minor})"
        )

    # Claude CLI
    if not shutil.which("claude"):
        errors.append(
            "Claude Code CLI not found on PATH.\n"
            "  Install: npm install -g @anthropic-ai/claude-code\n"
            "  Then authenticate: claude"
        )

    # uv (optional warning)
    if not shutil.which("uv"):
        errors.append(
            "uv not found on PATH.\n"
            "  Install: curl -LsSf https://astral.sh/uv/install.sh | sh"
        )

    return errors


@click.command()
@click.argument("prompt", required=False)
@click.option("--skip", type=click.Choice(["never", "next", "recommended", "always"]),
              default=None, help="Skip policy for layers")
@click.option("--gate", type=click.Choice(["human", "auto"]),
              default=None, help="Eval gate mode")
@click.option("--harness", type=click.Path(exists=True, file_okay=False),
              default=None, help="Custom harness directory")
@click.option("--plan", type=click.Choice(["max_5x", "max_20x"]),
              default=None, help="Max subscription plan for rate limiting")
@click.option("--dry-run", is_flag=True, help="Show plan without executing")
@click.option("--transcript", "show_transcript", is_flag=True,
              help="Show previous run transcripts")
@click.option("--sessions", "show_sessions", is_flag=True,
              help="List sessions from previous runs")
@click.option("--budget", is_flag=True, help="Show rate limit budget status")
@click.option("--bootstrap", "force_bootstrap", is_flag=True,
              help="Force project bootstrap (generate CLAUDE.md)")
@click.option("--no-bootstrap", "no_bootstrap", is_flag=True,
              help="Skip automatic bootstrap even if project is bare")
@click.option("--verbose", "-v", is_flag=True, help="Verbose logging")
@click.option("--extra-context", "-x", multiple=True,
              help="Extra context for layers. Format: 'layer:text' e.g. "
                   "'context:also check src/legacy/auth.py'")
@click.option("--attach", "-a", multiple=True, type=click.Path(exists=True),
              help="Attach file(s) to the pipeline. Contents injected into Context layer input.")
@click.option("--prompt-file", "-f", type=click.Path(exists=True),
              help="Read prompt from a file instead of the command line argument.")
def main(
    prompt: str | None,
    skip: str | None,
    gate: str | None,
    harness: str | None,
    plan: str | None,
    dry_run: bool,
    show_transcript: bool,
    show_sessions: bool,
    budget: bool,
    force_bootstrap: bool,
    no_bootstrap: bool,
    verbose: bool,
    extra_context: tuple[str, ...] = (),
    attach: tuple[str, ...] = (),
    prompt_file: str | None = None,
) -> None:
    """AI-Human Engineering Stack — layered cognitive pipeline for Claude Code.

    Run from within your project directory. The current directory is treated
    as the target project.

    \b
    Example:
        strata "refactor the auth module to use async/await"
        strata --skip=always --gate=auto "rename usr to user"
    """
    import logging

    if verbose:
        logging.basicConfig(level=logging.DEBUG, format="%(name)s: %(message)s")
    else:
        logging.basicConfig(level=logging.WARNING)

    # Resolve prompt from file if provided
    if prompt_file:
        prompt = Path(prompt_file).read_text(encoding="utf-8").strip()

    project_dir = os.path.abspath(os.getcwd())

    # Build CLI overrides dict
    cli_overrides: dict = {}
    if skip:
        cli_overrides["skip_policy"] = skip
    if gate:
        cli_overrides["eval_gate"] = gate
    if plan:
        cli_overrides["plan"] = plan

    # Load config
    config = load_config(
        project_dir=project_dir,
        cli_overrides=cli_overrides if cli_overrides else None,
        harness_override=harness,
    )

    # Handle info commands
    if budget:
        _show_budget(project_dir, config)
        return

    if show_transcript:
        _show_transcripts(project_dir)
        return

    if show_sessions:
        _show_sessions(project_dir)
        return

    # Require prompt for pipeline execution
    if not prompt:
        console.print("[error]No prompt provided.[/error]")
        console.print("Usage: strata \"your prompt here\"")
        console.print("       strata --help")
        raise SystemExit(1)

    # Parse extra context
    parsed_extra_context: dict[str, list[str]] = {}
    for item in extra_context:
        if ":" in item:
            layer_name, text = item.split(":", 1)
            layer_name = layer_name.strip().lower()
            if layer_name in LAYER_ORDER:
                parsed_extra_context.setdefault(layer_name, []).append(text.strip())
            else:
                console.print(f"[warning]Unknown layer '{layer_name}' in --extra-context, ignoring[/warning]")

    # Read attachments
    attachments: list[dict[str, str]] = []
    for filepath in attach:
        path = Path(filepath)
        try:
            content = path.read_text(encoding="utf-8")
            attachments.append({
                "filename": path.name,
                "path": str(path),
                "content": content[:50000],
            })
        except (OSError, UnicodeDecodeError) as exc:
            console.print(f"[warning]Could not read {filepath}: {exc}[/warning]")

    # Check prerequisites
    errors = _check_prerequisites()
    if errors:
        for err in errors:
            console.print(f"[error]{err}[/error]")
        raise SystemExit(1)

    # Ensure project dirs
    ensure_project_dirs(project_dir)

    # Dry run
    if dry_run:
        _dry_run(prompt, config)
        return

    # Run pipeline
    try:
        asyncio.run(_run(
            prompt, config, project_dir, harness, force_bootstrap, no_bootstrap,
            parsed_extra_context if parsed_extra_context else None,
            attachments if attachments else None,
        ))
    except KeyboardInterrupt:
        console.print("\n[yellow]Pipeline interrupted.[/yellow]")
        raise SystemExit(130)
    except SystemExit:
        raise
    except Exception as exc:
        console.print(Panel(
            f"[red]{exc}[/red]",
            title="[red bold]Pipeline Error[/red bold]",
            border_style="red",
        ))
        raise SystemExit(1)


async def _run(
    prompt: str,
    config: PipelineConfig,
    project_dir: str,
    harness_override: str | None,
    force_bootstrap: bool = False,
    no_bootstrap: bool = False,
    extra_context: dict[str, list[str]] | None = None,
    attachments: list[dict[str, str]] | None = None,
) -> None:
    """Async pipeline execution."""
    from app.agents.runner import check_claude_cli

    # Verify Claude CLI
    ok, msg = await check_claude_cli()
    if not ok:
        console.print(f"[error]{msg}[/error]")
        raise SystemExit(1)
    console.print(f"[dim]{msg}[/dim]")

    # Initialize tracker and transcript
    tracker = UsageTracker(project_dir)

    # Pre-flight check
    warning = tracker.pre_flight_check(
        config.model_dump(), len(prompt), config.plan
    )
    if warning:
        console.print(f"[warning]{warning}[/warning]")

    # Header
    console.print()
    console.print(
        Panel(
            f"[bold]Prompt:[/bold] {prompt[:200]}{'...' if len(prompt) > 200 else ''}\n"
            f"[dim]Project: {project_dir}[/dim]\n"
            f"[dim]Skip: {config.skip_policy} | Gate: {config.eval_gate} | Plan: {config.plan}[/dim]",
            title="[bold]AI-Human Engineering Stack[/bold]",
            border_style="bright_white",
        )
    )

    # Bootstrap: auto-generate CLAUDE.md for bare projects
    bootstrap_failed = False
    should_bootstrap = force_bootstrap or (not no_bootstrap and needs_bootstrap(project_dir))
    if should_bootstrap:
        console.print()
        console.print(
            Panel(
                "[dim]This project has no CLAUDE.md, README, or documentation.\n"
                "Running auto-investigation to generate project context...[/dim]",
                title="[yellow bold]Bootstrap[/yellow bold]",
                border_style="yellow",
            )
        )
        try:
            created_path, info = await run_bootstrap(project_dir)
            if created_path:
                console.print(
                    f"  [green]Created[/green] {created_path.relative_to(project_dir)}"
                )
                if info.get("project_name"):
                    console.print(
                        f"  [dim]Detected: {info['project_name']} "
                        f"({info.get('language', 'unknown')})[/dim]"
                    )
            else:
                console.print("  [dim]Bootstrap skipped (context files already exist).[/dim]")
        except Exception as exc:
            bootstrap_failed = True
            console.print(f"  [yellow bold]Bootstrap failed: {exc}[/yellow bold]")
            console.print(
                "  [yellow]WARNING: No CLAUDE.md or README exists. "
                "Context layer will have limited project awareness.[/yellow]"
            )

    # If bootstrap failed, append a note so Context layer knows it has no project docs
    if bootstrap_failed:
        prompt += (
            "\n\n[NOTE: Project bootstrap failed. No CLAUDE.md or README exists. "
            "The Context layer should investigate the project structure from scratch.]"
        )

    # Initialize transcript early so callbacks can log events during the run
    run_id = f"run_{uuid.uuid4().hex[:12]}"
    transcript = TranscriptWriter(project_dir, run_id)

    async def on_layer_start(layer, attempt):
        render_layer_start(console, layer, attempt)
        transcript.log_layer_start(layer, attempt)

    async def on_eval_start(layer):
        render_eval_start(console, layer)

    async def on_steering(layer, layer_output):
        return await prompt_layer_steering(console, layer, layer_output)

    async def on_auto_approve(layer, verdict):
        render_auto_approved(console, layer, verdict)
        transcript.log_auto_approve(layer)

    async def on_checkpoint(event):
        transcript.log_layer_result(
            event.layer,
            event.layer_output,
            event.state.sessions.get(event.layer),
            event.eval_verdict.model_dump(mode="json") if event.eval_verdict else None,
        )
        transcript.log_eval(event.layer, event.eval_verdict.model_dump(mode="json"))

        decision = await prompt_user_decision(console, event)

        transcript.log_decision(
            event.layer, decision.action.value, decision.feedback
        )

        return decision

    async def on_interrupt(event):
        decision = await prompt_interrupt_decision(console, event)
        transcript.log_event("interrupt", {
            "layer": event.layer,
            "action": decision.action,
        })
        return decision

    # Run the pipeline
    try:
        final_state = await run_pipeline(
            user_prompt=prompt,
            config=config,
            project_dir=project_dir,
            on_checkpoint=on_checkpoint,
            on_layer_start=on_layer_start,
            on_eval_start=on_eval_start,
            on_auto_approve=on_auto_approve,
            on_interrupt=on_interrupt,
            on_steering=on_steering,
            harness_override=harness_override,
            usage_tracker=tracker,
            run_id=run_id,
            extra_context=extra_context,
            attachments=attachments,
        )
    except BaseException:
        transcript.write_partial()
        raise

    transcript_path = transcript.finalize(final_state)

    # Final output
    console.print()
    coherence = final_state.layers.get("coherence")
    if coherence and coherence.status == "approved" and coherence.output:
        final_output = coherence.output.get("final_output", "")
        console.print(
            Panel(
                str(final_output),
                title="[bold green]Final Output[/bold green]",
                border_style="green",
                padding=(1, 2),
            )
        )
    else:
        # Show the last approved layer's output with explanation
        found = False
        for name in reversed(LAYER_ORDER):
            lr = final_state.layers.get(name)
            if lr and lr.status == "approved" and lr.output:
                reason = "Pipeline did not reach Coherence" if name != "coherence" else "Coherence had no final_output"
                console.print(
                    f"[yellow]NOTE: {reason} — showing output from {name.title()} layer.[/yellow]"
                )
                console.print(
                    Panel(
                        json.dumps(lr.output, indent=2, default=str),
                        title=f"[bold yellow]Partial: {name.title()} Layer Output[/bold yellow]",
                        border_style="yellow",
                    )
                )
                found = True
                break
        if not found:
            console.print("[yellow]No layers were approved. Pipeline produced no output.[/yellow]")

    # --- Run summary ---
    console.print()
    hourly = tracker.get_hourly_usage()
    remaining = tracker.estimate_remaining_pct(config.plan)

    layer_count = sum(1 for lr in final_state.layers.values() if lr and lr.status in ("approved", "skipped"))
    total_in = 0
    total_out = 0
    call_count = 0
    for event in transcript._events:
        if event.get("type") in ("layer_result", "eval"):
            usage = event.get("usage")
            if usage and isinstance(usage, dict):
                total_in += usage.get("tokens_in", 0)
                total_out += usage.get("tokens_out", 0)
                call_count += 1

    console.print(
        f"[dim]Layers: {layer_count}/5 | API calls: {call_count} | "
        f"Tokens: {total_in:,} in / {total_out:,} out | "
        f"Budget remaining: ~{int(remaining * 100)}%[/dim]"
    )

    console.print(f"\n[dim]Run: {final_state.run_id}[/dim]")
    console.print(f"[dim]Transcript: {transcript_path}[/dim]")


def _dry_run(prompt: str, config: PipelineConfig) -> None:
    """Show what would happen without executing."""
    console.print("\n[bold]DRY RUN[/bold] — no agents will execute\n")

    enabled = [
        name for name in LAYER_ORDER if config.get_layer(name).enabled
    ]

    for name in LAYER_ORDER:
        cfg = config.get_layer(name)
        status = "[green]enabled[/green]" if cfg.enabled else "[dim]disabled[/dim]"
        tools = ", ".join(cfg.allowed_tools)
        extra = ""
        if name == "coherence" and cfg.setting_sources:
            extra = f" [dim](setting_sources: {', '.join(cfg.setting_sources)})[/dim]"
        console.print(
            f"  {LAYER_ORDER.index(name) + 1}. [bold]{name.title()}[/bold] "
            f"[{cfg.model}] {status} — tools: {tools}{extra}"
        )

    console.print(f"\n  Eval: [{config.eval.model}] — tools: {', '.join(config.eval.allowed_tools)}")
    console.print(f"\n  Skip policy: {config.skip_policy}")
    console.print(f"  Eval gate: {config.eval_gate}")
    console.print(f"  Max retries: {config.max_retries_per_layer}")
    console.print(f"\n  Total calls: ~{len(enabled) * 2} (layers + evals)")
    console.print(f"  Prompt length: {len(prompt)} chars")


def _show_budget(project_dir: str, config: PipelineConfig) -> None:
    """Display rate limit budget status."""
    tracker = UsageTracker(project_dir)
    hourly = tracker.get_hourly_usage()
    remaining = tracker.estimate_remaining_pct(config.plan)

    console.print(f"\n[bold]Rate Limit Budget ({config.plan})[/bold]")
    console.print(f"  Input tokens (last hour):  {hourly['input']:,}")
    console.print(f"  Output tokens (last hour): {hourly['output']:,}")
    console.print(f"  Estimated remaining: ~{int(remaining * 100)}%")


def _show_transcripts(project_dir: str) -> None:
    """List previous run transcripts."""
    transcript_dir = Path(project_dir) / ".stack" / "transcripts"
    if not transcript_dir.is_dir():
        console.print("[dim]No transcripts found.[/dim]")
        return

    files = sorted(transcript_dir.glob("*.json"), reverse=True)
    if not files:
        console.print("[dim]No transcripts found.[/dim]")
        return

    console.print(f"\n[bold]Transcripts ({len(files)})[/bold]")
    for f in files[:10]:
        try:
            data = json.loads(f.read_text())
            prompt_preview = data.get("original_prompt", "")[:60]
            console.print(f"  {f.stem}  \"{prompt_preview}\"")
        except (json.JSONDecodeError, KeyError):
            console.print(f"  {f.stem}  [dim](corrupt)[/dim]")


def _show_sessions(project_dir: str) -> None:
    """List sessions from previous runs."""
    transcript_dir = Path(project_dir) / ".stack" / "transcripts"
    if not transcript_dir.is_dir():
        console.print("[dim]No sessions found.[/dim]")
        return

    files = sorted(transcript_dir.glob("*.json"), reverse=True)
    for f in files[:5]:
        try:
            data = json.loads(f.read_text())
            sessions = data.get("sessions", {})
            if sessions:
                console.print(f"\n[bold]{f.stem}[/bold]")
                for layer, sid in sessions.items():
                    console.print(f"  {layer}: {sid}")
        except (json.JSONDecodeError, KeyError):
            pass


if __name__ == "__main__":
    main()
