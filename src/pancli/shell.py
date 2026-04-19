"""Interactive shell that reuses the main Typer application."""

from __future__ import annotations

import asyncio
import os
import shlex
import subprocess
from pathlib import Path

import click
from prompt_toolkit import PromptSession
from prompt_toolkit.history import InMemoryHistory
from rich.panel import Panel
import typer
from typer.main import get_command

from .api import AsyncApiManager
from .main import _login, _normalize_remote_path, app
from .theme import UIOptions, create_console

SHELL_HELP_LINES = [
    "Shell commands:",
    "  help [command]      Show shell help or command help.",
    "  exit, quit          Leave the interactive shell.",
    "  pwd                 Print current remote directory.",
    "  cd [path]           Change remote working directory.",
    "  lpwd                Print current local directory.",
    "  lcd [path]          Change local working directory.",
    "  lls [path]          List local files.",
    "  !<command>          Run a local shell command.",
    "",
    "Remote commands:",
    "  ls [path]           List files in a remote directory.",
    "  tree [path]         Show a remote directory tree.",
    "  stat <path>         Show metadata for a file or directory.",
    "  find <keyword>      Search by keyword under a path.",
    "  search <keyword>    Alias of find.",
    "  quota               Show storage quota usage.",
    "  mkdir <path>        Create a remote directory.",
    "  touch <path>        Create an empty remote file.",
    "  rm <path>           Delete a remote file or directory.",
    "  mv <src> <dst>      Move or rename a remote item.",
    "  cp <src> <dst>      Copy a remote item.",
    "  cat <path>          Print a remote text file.",
    "  link ...            Manage share links.",
    "  upload ...          Upload files; remote target defaults to current remote dir.",
    "  download ...        Download files; local target defaults to current local dir.",
    "  revisions <path>    List file revision history.",
    "  restore-revision    Restore a file revision.",
    "  trash ...           Trash-related commands.",
    "",
    "Tips:",
    "  Use '<command> --help' for full parameter help.",
    "  In shell, '<command> -h' is treated as help.",
]


class PanShell:
    def __init__(self, ui: UIOptions | None = None) -> None:
        self.ui = ui or UIOptions()
        self.console = create_console(self.ui, force_terminal=True)
        self.remote_cwd = "/"
        self.local_cwd = str(Path.cwd())
        self.home_root = "/"
        self.manager: AsyncApiManager | None = None

    async def login(self) -> None:
        self.manager, self.home_root = await _login(self.console)
        self.remote_cwd = self.home_root

    async def close(self) -> None:
        if self.manager is not None:
            await self.manager.close()

    async def run(self) -> None:
        await self.login()
        session = PromptSession(history=InMemoryHistory())
        try:
            while True:
                try:
                    text = await session.prompt_async(f"PanCLI [{self.remote_cwd}] $ ")
                except EOFError:
                    break
                except KeyboardInterrupt:
                    self.console.print()
                    continue
                if not text.strip():
                    continue
                if await self.handle(text):
                    break
        finally:
            await self.close()

    async def _show_command_help(self, command_name: str) -> None:
        previous = os.environ.get("PANCLI_REMOTE_CWD")
        os.environ["PANCLI_REMOTE_CWD"] = self.remote_cwd
        try:
            await asyncio.to_thread(
                get_command(app).main,
                args=[command_name, "--help"],
                prog_name="pancli",
                standalone_mode=False,
            )
        except click.ClickException as exc:
            self.console.print(f"command error: {exc.format_message()}", style="error")
        except typer.Exit:
            pass
        except SystemExit:
            pass
        finally:
            if previous is None:
                os.environ.pop("PANCLI_REMOTE_CWD", None)
            else:
                os.environ["PANCLI_REMOTE_CWD"] = previous

    def _print_help(self) -> None:
        self.console.print(
            Panel.fit(
                "\n".join(SHELL_HELP_LINES),
                title="PanCLI Shell",
                border_style="accent",
                padding=(0, 1),
            )
        )

    async def handle(self, text: str) -> bool:
        if text in {"exit", "quit"}:
            return True
        if text in {"help", "?"}:
            self._print_help()
            return False
        if text.startswith("!"):
            completed = subprocess.run(text[1:], cwd=self.local_cwd, shell=True)
            if completed.returncode != 0:
                self.console.print(f"local command failed: {completed.returncode}")
            return False
        try:
            argv = shlex.split(text)
        except ValueError as exc:
            self.console.print(f"parse error: {exc}", style="error")
            return False
        if not argv:
            return False
        if argv[0] in {"help", "?"}:
            if len(argv) == 1:
                self._print_help()
                return False
            await self._show_command_help(argv[1])
            return False
        cmd = argv[0]
        if cmd == "pwd":
            self.console.print(self.remote_cwd)
            return False
        if cmd == "cd":
            target = argv[1] if len(argv) > 1 else "."
            candidate = _normalize_remote_path(target, self.home_root)
            info = await self.manager.get_resource_info_by_path(candidate.strip("/")) if self.manager else None
            if info is None or not info.is_dir:
                self.console.print(f"not a directory: {candidate}")
                return False
            self.remote_cwd = candidate
            return False
        if cmd == "lpwd":
            self.console.print(self.local_cwd)
            return False
        if cmd == "lcd":
            target = Path(argv[1] if len(argv) > 1 else self.local_cwd).expanduser().resolve()
            if not target.exists() or not target.is_dir():
                self.console.print(f"not a directory: {target}")
                return False
            self.local_cwd = str(target)
            return False
        if cmd == "lls":
            target = Path(argv[1] if len(argv) > 1 else self.local_cwd).expanduser().resolve()
            if not target.exists():
                self.console.print(f"missing: {target}")
                return False
            for item in sorted(target.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower())):
                suffix = "/" if item.is_dir() else ""
                self.console.print(item.name + suffix)
            return False
        previous = os.environ.get("PANCLI_REMOTE_CWD")
        os.environ["PANCLI_REMOTE_CWD"] = self.remote_cwd
        try:
            if len(argv) == 2 and argv[1] == "-h":
                argv = [argv[0], "--help"]
            # Run Typer commands in a worker thread so command handlers can
            # safely use asyncio.run() without nesting inside the shell loop.
            await asyncio.to_thread(
                get_command(app).main,
                args=argv,
                prog_name="pancli",
                standalone_mode=False,
            )
        except click.ClickException as exc:
            self.console.print(f"command error: {exc.format_message()}", style="error")
        except click.Abort:
            self.console.print("aborted", style="warning")
        except typer.Exit as exc:
            if exc.exit_code not in (None, 0):
                self.console.print(f"command failed with exit code {exc.exit_code}", style="error")
        except SystemExit as exc:
            if exc.code not in (None, 0):
                self.console.print(f"command failed with exit code {exc.code}", style="error")
        except Exception as exc:
            self.console.print(f"unexpected error: {exc}", style="error")
        finally:
            if previous is None:
                os.environ.pop("PANCLI_REMOTE_CWD", None)
            else:
                os.environ["PANCLI_REMOTE_CWD"] = previous
        return False


def run_interactive_shell(ui: UIOptions | None = None) -> None:
    try:
        asyncio.run(PanShell(ui).run())
    except KeyboardInterrupt:
        pass
