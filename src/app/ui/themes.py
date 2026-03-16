"""Color schemes and formatting constants for Rich console output."""

from rich.style import Style
from rich.theme import Theme

# Layer colors — bottom to top, cool to warm
LAYER_COLORS: dict[str, str] = {
    "prompt": "blue",
    "context": "cyan",
    "intent": "green",
    "judgment": "yellow",
    "coherence": "magenta",
}

# Verdict colors
VERDICT_STYLES: dict[str, Style] = {
    "pass": Style(color="green", bold=True),
    "concern": Style(color="yellow", bold=True),
    "fail": Style(color="red", bold=True),
}

VERDICT_ICONS: dict[str, str] = {
    "pass": "✓",
    "concern": "⚠",
    "fail": "✗",
}

STACK_THEME = Theme({
    "layer.prompt": "blue",
    "layer.context": "cyan",
    "layer.intent": "green",
    "layer.judgment": "yellow",
    "layer.coherence": "magenta",
    "verdict.pass": "green bold",
    "verdict.concern": "yellow bold",
    "verdict.fail": "red bold",
    "header": "bold white",
    "dim": "dim",
    "warning": "yellow",
    "error": "red bold",
})
