"""Checkpoint display — Rich panels for layer results, eval verdicts, and user gating."""

from __future__ import annotations

import json

from rich.console import Console
from rich.json import JSON
from rich.markup import escape
from rich.panel import Panel
from rich.prompt import Prompt
from rich.rule import Rule
from rich.text import Text

from app.pipeline import CheckpointAction, CheckpointEvent, InterruptDecision, InterruptEvent, UserDecision
from app.state import LAYER_ORDER, EvalVerdict, LayerName, layer_index
from app.ui.themes import LAYER_COLORS, VERDICT_ICONS, VERDICT_STYLES


def _print_menu_item(console: Console, key: str, label: str) -> None:
    """Render a menu item with a bold key indicator, avoiding Rich markup issues.

    Uses Text objects so bracket characters are never interpreted as markup tags
    (e.g. ``[s]`` would otherwise trigger strikethrough).
    """
    line = Text("  ")
    line.append(f"[{key}]", style="bold")
    line.append(f" {label}")
    console.print(line)


LAYER_THINKING: dict[str, str] = {
    "prompt": "Parsing and clarifying the user request into a structured task specification...",
    "context": "Exploring the codebase \u2014 reading files, checking git history, mapping dependencies...",
    "intent": "Defining success criteria, constraints, and scope boundaries...",
    "judgment": "Stress-testing assumptions \u2014 finding risks, edge cases, and potential issues...",
    "coherence": "Producing the final output, addressing all risks and aligning with project standards...",
}

EVAL_THINKING: dict[str, str] = {
    "prompt": "Evaluating task spec clarity and completeness...",
    "context": "Evaluating gathered context for relevance and coverage...",
    "intent": "Evaluating success criteria for specificity and testability...",
    "judgment": "Evaluating risk analysis for thoroughness and practicality...",
    "coherence": "Evaluating final output for correctness and alignment...",
}


def _layer_number(layer: LayerName) -> int:
    return layer_index(layer) + 1


def _format_usage(usage_record) -> str:
    if not usage_record:
        return ""
    return f"{usage_record.tokens_in:,} in / {usage_record.tokens_out:,} out"


# What each layer hands off to the next, and what the next layer does with it
LAYER_HANDOFF: dict[str, str] = {
    "prompt": (
        "Next: [bold]Context[/bold] will use this task spec to search the "
        "codebase — reading files, checking git history, finding dependencies. "
        "Is the task description clear enough for targeted exploration?"
    ),
    "context": (
        "Next: [bold]Intent[/bold] will use these findings to define precise "
        "success criteria, constraints, and scope boundaries. "
        "Is the gathered context sufficient to define what 'done' looks like?"
    ),
    "intent": (
        "Next: [bold]Judgment[/bold] will stress-test these goals — challenging "
        "assumptions, finding risks, identifying edge cases. "
        "Are the success criteria and constraints concrete enough to critique?"
    ),
    "judgment": (
        "Next: [bold]Coherence[/bold] will produce the final output, addressing "
        "every risk above and aligning with project standards (CLAUDE.md). "
        "Are the risks and edge cases specific enough to act on?"
    ),
    "coherence": (
        "This is the [bold]final layer[/bold]. Approving produces the pipeline output."
    ),
}


def render_checkpoint(console: Console, event: CheckpointEvent) -> None:
    """Display a checkpoint panel for a completed layer."""
    layer = event.layer
    num = _layer_number(layer)
    color = LAYER_COLORS.get(layer, "white")
    verdict = event.eval_verdict
    v_icon = VERDICT_ICONS.get(verdict.verdict, "?")
    v_style = VERDICT_STYLES.get(verdict.verdict)

    # Get attempt info from state
    lr = event.state.layers.get(layer)
    attempt = lr.attempt if lr else 1

    title = f"LAYER {num}: {layer.upper()} — Complete"
    subtitle = f"[attempt {attempt}]"

    # --- Usage bar ---
    usage_line = ""
    if lr and lr.usage:
        usage_line = (
            f"[dim]Model: {lr.usage.model} | "
            f"Session: {lr.usage.session_id[:12] if lr.usage.session_id else 'n/a'} | "
            f"{_format_usage(lr.usage)}[/dim]"
        )

    # --- Build the panel ---
    console.print()

    # Layer header panel
    header_parts = []
    if usage_line:
        header_parts.append(usage_line)
    panel_header = Panel(
        "\n".join(header_parts) if header_parts else f"[dim]Layer {num} complete[/dim]",
        title=f"[{color} bold]{title}[/{color} bold]",
        subtitle=f"[dim]{subtitle}[/dim]",
        border_style=color,
        padding=(0, 2),
    )
    console.print(panel_header)

    # --- Full layer output ---
    console.print()
    console.print(f"  [{color} bold]Output[/{color} bold]")

    output = event.layer_output
    if "_parse_error" in output:
        console.print(f"  [red]Parse error:[/red] {output['_parse_error']}")
        if "_raw" in output:
            console.print(Panel(
                str(output["_raw"])[:1000],
                title="[red]Raw output[/red]",
                border_style="red",
            ))
    else:
        # Render the full JSON output with syntax highlighting
        try:
            json_str = json.dumps(output, indent=2, default=str)
            console.print(Panel(
                JSON(json_str),
                border_style="dim",
                padding=(0, 1),
            ))
        except Exception:
            console.print(Panel(escape(str(output)[:2000]), border_style="dim"))

    # --- Eval verdict ---
    console.print()
    verdict_text = Text()
    verdict_text.append(f"  EVAL: {v_icon} {verdict.verdict.upper()} ", style=v_style)
    verdict_text.append(f"— {verdict.summary}", style="default")
    console.print(verdict_text)

    if event.eval_failed:
        console.print(f"  [yellow bold]WARNING: Eval agent failed \u2014 verdict is synthetic.[/yellow bold]")

    if verdict.findings:
        console.print()
        for finding in verdict.findings:
            console.print(f"    \u2022 {escape(finding)}")

    if verdict.skip_recommendation:
        console.print(f"\n    [dim]Skip recommendation: {escape(verdict.skip_recommendation)}[/dim]")

    # --- Handoff context: what next layer needs ---
    console.print()
    handoff = LAYER_HANDOFF.get(layer, "")
    if handoff:
        console.print(Panel(
            handoff,
            title="[bold]What happens next[/bold]",
            border_style="dim",
            padding=(0, 2),
        ))

    # --- Rate limit warning ---
    if event.rate_limit_warning:
        console.print(f"\n  [yellow]RATE LIMIT: {event.rate_limit_warning}[/yellow]")

    # --- Action menu ---
    console.print()
    for action in event.available_actions:
        if action == CheckpointAction.APPROVE:
            next_layer = ""
            idx = layer_index(layer)
            if idx < len(LAYER_ORDER) - 1:
                next_layer = f" \u2192 {LAYER_ORDER[idx + 1].title()}"
            _print_menu_item(console, "a", f"Approve{next_layer}")
        elif action == CheckpointAction.SKIP_TO:
            targets = LAYER_ORDER[layer_index(layer) + 2:]
            if targets:
                target_str = " / ".join(targets)
                _print_menu_item(console, "s", f"Skip to: {escape(target_str)}")
        elif action == CheckpointAction.REPROMPT_CURRENT:
            session_hint = ""
            if event.state.sessions.get(layer):
                sid = event.state.sessions[layer]
                session_hint = f" (resumes session {sid[:12]})"
            _print_menu_item(console, "r", f"Reprompt{session_hint}")
        elif action == CheckpointAction.REPROMPT_LOWER:
            lower = LAYER_ORDER[:layer_index(layer)]
            if lower:
                lower_str = " / ".join(lower)
                _print_menu_item(console, "b", f"Back to: {escape(lower_str)}")
        elif action == CheckpointAction.ABORT:
            _print_menu_item(console, "q", "Abort")


def render_auto_approved(
    console: Console, layer: LayerName, verdict: EvalVerdict
) -> None:
    """One-line display for auto-approved layers."""
    color = LAYER_COLORS.get(layer, "white")
    v_icon = VERDICT_ICONS.get(verdict.verdict, "?")
    console.print(
        f"  {v_icon} [{color}]{layer.upper()}[/{color}] "
        f"[dim]{verdict.verdict}[/dim] \u2014 \"{escape(verdict.summary)}\""
    )


def render_layer_start(console: Console, layer: LayerName, attempt: int) -> None:
    """Display when a layer begins processing, with a thinking summary."""
    num = _layer_number(layer)
    color = LAYER_COLORS.get(layer, "white")
    suffix = f" (attempt {attempt})" if attempt > 1 else ""
    console.print(
        f"\n[{color} bold]\u25b6 Layer {num}: {layer.upper()}{suffix}[/{color} bold]",
    )
    thinking = LAYER_THINKING.get(layer, "")
    if thinking:
        console.print(f"  [dim]{thinking}[/dim]")


def render_eval_start(console: Console, layer: LayerName) -> None:
    """Display when eval begins for a layer."""
    color = LAYER_COLORS.get(layer, "white")
    thinking = EVAL_THINKING.get(layer, "Evaluating layer output...")
    console.print(f"  [{color}]\u2714 Layer complete.[/{color}] [dim]{thinking}[/dim]")


async def prompt_user_decision(
    console: Console, event: CheckpointEvent
) -> UserDecision:
    """Interactive prompt for the user at a checkpoint."""
    render_checkpoint(console, event)

    # Map actions to shortcut keys
    action_keys = {
        CheckpointAction.APPROVE: "a",
        CheckpointAction.SKIP_TO: "s",
        CheckpointAction.REPROMPT_CURRENT: "r",
        CheckpointAction.REPROMPT_LOWER: "b",
        CheckpointAction.ABORT: "q",
    }
    valid_keys = [action_keys[a] for a in event.available_actions if a in action_keys]

    while True:
        choice = Prompt.ask(
            "\n[bold]Action[/bold]",
            choices=valid_keys,
            default="a",
            console=console,
        )

        if choice == "a":
            return UserDecision(action=CheckpointAction.APPROVE)

        elif choice == "s":
            idx = layer_index(event.layer)
            targets = LAYER_ORDER[idx + 2:]
            if not targets:
                console.print("[dim]No layers to skip to.[/dim]")
                continue
            if len(targets) == 1:
                target = targets[0]
            else:
                target_input = Prompt.ask(
                    "Skip to which layer?",
                    choices=list(targets),
                    console=console,
                )
                target = target_input  # type: ignore
            return UserDecision(
                action=CheckpointAction.SKIP_TO, target_layer=target
            )

        elif choice == "r":
            feedback = Prompt.ask(
                "Feedback for retry", default="Please revise.", console=console
            )
            return UserDecision(
                action=CheckpointAction.REPROMPT_CURRENT, feedback=feedback
            )

        elif choice == "b":
            lower = LAYER_ORDER[: layer_index(event.layer)]
            if not lower:
                console.print("[dim]No lower layers to go back to.[/dim]")
                continue
            if len(lower) == 1:
                target = lower[0]
            else:
                target_input = Prompt.ask(
                    "Go back to which layer?",
                    choices=list(lower),
                    console=console,
                )
                target = target_input  # type: ignore
            feedback = Prompt.ask(
                "Feedback (optional)", default="", console=console
            )
            return UserDecision(
                action=CheckpointAction.REPROMPT_LOWER,
                target_layer=target,
                feedback=feedback or None,
            )

        elif choice == "q":
            return UserDecision(action=CheckpointAction.ABORT)


# Which output fields from each layer are steerable, with user-facing labels.
# Each entry: (json_key, singular_label, plural_label, action_verb)
STEERABLE_FIELDS: dict[str, list[tuple[str, str, str, str]]] = {
    "prompt": [("ambiguities", "ambiguity", "ambiguities", "Clarify")],
    "context": [("gaps", "gap", "gaps", "Address")],
    "intent": [
        ("out_of_scope", "exclusion", "exclusions", "Override"),
        ("constraints", "constraint", "constraints", "Adjust"),
    ],
    "judgment": [
        ("missing_considerations", "consideration", "considerations", "Respond to"),
        ("assumptions_challenged", "challenged assumption", "challenged assumptions", "Respond to"),
    ],
    # coherence is the final layer — no steering needed
}


def _extract_steerable_items(layer: str, layer_output: dict) -> list[tuple[str, str]]:
    """Extract all steerable items from a layer's output.

    Returns list of (category_label, item_text) tuples.
    """
    fields = STEERABLE_FIELDS.get(layer, [])
    items: list[tuple[str, str]] = []
    for json_key, singular, plural, verb in fields:
        raw = layer_output.get(json_key, [])
        if not raw:
            continue
        for entry in raw:
            # Some fields are dicts (e.g. assumptions_challenged), some are strings
            if isinstance(entry, dict):
                # Pick the most descriptive field
                text = entry.get("assumption") or entry.get("risk") or entry.get("criterion")
                detail = entry.get("challenge") or entry.get("mitigation") or entry.get("verification")
                if text and detail:
                    display = f"{text} — {detail}"
                else:
                    display = text or str(entry)
            else:
                display = str(entry)
            items.append((f"{verb}", display))
    return items


async def prompt_layer_steering(
    console: Console, layer: str, layer_output: dict
) -> dict[str, str]:
    """Walk the user through steerable items from any layer's output.

    Returns a dict mapping each item to the user's response (empty if skipped).
    """
    items = _extract_steerable_items(layer, layer_output)
    if not items:
        return {}

    color = LAYER_COLORS.get(layer, "white")
    console.print()
    console.print(Rule(
        title=f"[{color} bold]Steering: {layer.title()}[/{color} bold]",
        style=color,
    ))
    console.print(
        f"  [dim]{len(items)} item{'s' if len(items) != 1 else ''} "
        f"you can steer. Press Enter to skip any.[/dim]"
    )
    console.print()

    resolutions: dict[str, str] = {}
    for i, (verb, display) in enumerate(items, 1):
        console.print(
            f"  [{color} bold]{i}/{len(items)}[/{color} bold] "
            f"[dim]{verb}:[/dim] {escape(display)}"
        )
        answer = Prompt.ask(
            f"    [dim]Your input (Enter to skip)[/dim]",
            default="",
            console=console,
        )
        resolutions[display] = answer.strip()
        console.print()

    provided = sum(1 for v in resolutions.values() if v)
    console.print(
        f"  [dim]{provided} steering input{'s' if provided != 1 else ''} provided, "
        f"{len(items) - provided} skipped.[/dim]"
    )
    console.print()
    return resolutions


async def prompt_interrupt_decision(
    console: Console, event: InterruptEvent
) -> InterruptDecision:
    """Prompt the user after they interrupt a running layer with Ctrl+C."""
    num = _layer_number(event.layer)
    color = LAYER_COLORS.get(event.layer, "white")

    console.print()
    console.print(Panel(
        f"[{color} bold]Layer {num}: {event.layer.upper()}[/{color} bold] was interrupted.",
        title="[yellow bold]Interrupted[/yellow bold]",
        border_style="yellow",
        padding=(0, 2),
    ))

    choices = ["r", "q"]
    _print_menu_item(console, "r", "Retry this layer")

    if event.can_go_back and event.previous_layer:
        choices.insert(1, "b")
        prev_num = _layer_number(event.previous_layer)
        _print_menu_item(
            console, "b",
            f"Back to Layer {prev_num}: {event.previous_layer.title()} checkpoint",
        )

    _print_menu_item(console, "q", "Abort pipeline")

    choice = Prompt.ask(
        "\n[bold]Action[/bold]",
        choices=choices,
        default="r",
        console=console,
    )

    if choice == "r":
        return InterruptDecision(action="retry")
    elif choice == "b":
        return InterruptDecision(
            action="back", target_layer=event.previous_layer
        )
    else:
        return InterruptDecision(action="abort")
