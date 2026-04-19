"""Console and semantic theme helpers."""

from __future__ import annotations

from dataclasses import dataclass

from rich.console import Console
from rich.theme import Theme

from .models import ThemeMode

_THEMES: dict[str, dict[str, str]] = {
    "dark": {
        "text": "white",
        "muted": "bright_black",
        "accent": "cyan",
        "success": "green",
        "warning": "yellow",
        "error": "bold red",
        "path": "bright_cyan",
        "progress_bar": "cyan",
    },
    "light": {
        "text": "black",
        "muted": "dim",
        "accent": "blue",
        "success": "green",
        "warning": "dark_orange",
        "error": "bold red",
        "path": "blue",
        "progress_bar": "blue",
    },
    "plain": {
        "text": "",
        "muted": "",
        "accent": "",
        "success": "",
        "warning": "",
        "error": "",
        "path": "",
        "progress_bar": "",
    },
}


@dataclass(slots=True)
class UIOptions:
    theme_mode: str = ThemeMode.AUTO.value
    plain: bool = False
    no_color: bool = False
    force_terminal: bool | None = None


def resolve_theme_name(mode: str, *, plain: bool = False, no_color: bool = False) -> str:
    if plain or no_color or mode == ThemeMode.PLAIN.value:
        return "plain"
    if mode == ThemeMode.LIGHT.value:
        return "light"
    return "dark"


def create_console(
    options: UIOptions | None = None,
    *,
    stderr: bool = False,
    force_terminal: bool | None = None,
) -> Console:
    opts = options or UIOptions()
    effective_force_terminal = force_terminal if force_terminal is not None else opts.force_terminal
    theme_name = resolve_theme_name(
        opts.theme_mode,
        plain=opts.plain,
        no_color=opts.no_color,
    )
    if theme_name == "plain":
        return Console(
            stderr=stderr,
            no_color=True,
            force_terminal=False if effective_force_terminal is None else effective_force_terminal,
        )
    return Console(
        stderr=stderr,
        force_terminal=effective_force_terminal,
        theme=Theme(_THEMES[theme_name]),
    )
