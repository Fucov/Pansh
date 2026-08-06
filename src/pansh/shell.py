"""Interactive shell that reuses the main Typer application."""

from __future__ import annotations

import asyncio
import os
import shlex
import subprocess
from pathlib import Path

import click
import typer
from prompt_toolkit import PromptSession
from prompt_toolkit.completion import Completer, Completion
from prompt_toolkit.document import Document
from prompt_toolkit.history import InMemoryHistory
from rich.console import Group
from rich.panel import Panel
from rich.table import Table
from rich.text import Text
from typer.main import get_command

from .api import AsyncApiManager
from .main import (
    AppState,
    ENV_LOCAL_CWD,
    ENV_REMOTE_CWD,
    LEGACY_ENV_LOCAL_CWD,
    LEGACY_ENV_REMOTE_CWD,
    _login,
    _normalize_remote_path,
    app,
)
from .theme import create_console

INTERACTIVE_COMMANDS = [
    ("help [command]", "显示 shell 帮助，或查看某个 command 的 --help。"),
    ("clear, cls", "清空终端显示。"),
    ("exit, quit", "退出交互式 shell。"),
    ("pwd", "显示当前远端目录。"),
    ("cd [路径]", "切换当前远端目录。"),
    ("lpwd", "显示当前本地目录。"),
    ("lcd [路径]", "切换当前本地目录。"),
    ("lls [路径]", "列出本地文件。"),
    ("!<command>", "执行本地 shell command；!cd 会同步更新本地工作目录。"),
]

VISIBLE_COMMANDS = [
    ("logout", "注销当前会话；persistent 会清本地 token，once 只结束临时会话。"),
    ("whoami", "显示当前登录信息。"),
    ("config ...", "查看或修改本地设置。"),
    ("ls [路径]", "列出远端目录内容。"),
    ("tree [路径]", "以树形方式显示远端目录。"),
    ("stat <路径>", "查看文件或目录元数据。"),
    ("find <keyword>", "在远端路径下按名称查找。"),
    ("mkdir <路径>", "创建远端目录。"),
    ("touch <路径>", "创建空文件。"),
    ("rm <路径>", "删除远端文件；目录需配合 --recursive。"),
    ("mv <源> <目标>", "移动或重命名远端项。"),
    ("cp <源> <目标>", "复制远端项。"),
    ("cat <路径>", "输出远端文本文件内容。"),
    ("upload ...", "上传文件；默认目标是当前远端目录。"),
    ("download ...", "下载文件；默认目标是当前本地目录。"),
]

COMMAND_NAMES = sorted({item[0].split()[0] for item in INTERACTIVE_COMMANDS + VISIBLE_COMMANDS})
LOCAL_SHELL_COMMANDS = ("cd", "dir", "ls", "pwd")


def _append_sep(path: Path, text: str) -> str:
    if path.is_dir() and not text.endswith(("/", "\\")):
        return text + os.sep
    return text


class LocalPathCompleter(Completer):
    def __init__(self, shell: "PanShell") -> None:
        self.shell = shell

    def _iter_local_matches(self, raw: str, *, directories_only: bool) -> list[Completion]:
        base = Path(self.shell.local_cwd)
        expanded = Path(raw).expanduser()
        candidate = expanded if expanded.is_absolute() else base / expanded
        parent = candidate if raw.endswith(("/", "\\")) else candidate.parent
        prefix = "" if raw.endswith(("/", "\\")) else candidate.name
        try:
            parent = parent.resolve()
        except OSError:
            return []
        if not parent.exists() or not parent.is_dir():
            return []
        results: list[Completion] = []
        for entry in sorted(parent.iterdir(), key=lambda item: (not item.is_dir(), item.name.lower())):
            if directories_only and not entry.is_dir():
                continue
            if prefix and not entry.name.lower().startswith(prefix.lower()):
                continue
            display = _append_sep(entry, entry.name)
            start_position = -len(prefix)
            results.append(Completion(display, start_position=start_position, display=display))
        return results

    def _yield_command_completions(self, current: str):
        for name in COMMAND_NAMES:
            if name.startswith(current):
                yield Completion(name, start_position=-len(current), display=name)

    def _yield_local_dir_completions(self, current: str):
        for item in self._iter_local_matches(current, directories_only=True):
            yield item

    def _yield_local_path_completions(self, current: str):
        for item in self._iter_local_matches(current, directories_only=False):
            yield item

    def get_completions(self, document: Document, complete_event):
        text = document.text_before_cursor
        stripped = text.lstrip()
        if not stripped:
            yield from self._yield_local_dir_completions("")
            yield from self._yield_command_completions("")
            return

        try:
            argv = shlex.split(text)
            trailing_space = text.endswith(" ")
        except ValueError:
            return

        current = "" if trailing_space else (argv[-1] if argv else "")
        tokens = argv if trailing_space else argv[:-1]

        if text.startswith("!"):
            bang_text = text[1:]
            try:
                bang_argv = shlex.split(bang_text)
                bang_trailing = bang_text.endswith(" ")
            except ValueError:
                return
            bang_current = "" if bang_trailing else (bang_argv[-1] if bang_argv else "")
            bang_tokens = bang_argv if bang_trailing else bang_argv[:-1]
            if not bang_tokens:
                for name in LOCAL_SHELL_COMMANDS:
                    if name.startswith(bang_current):
                        yield Completion(name, start_position=-len(bang_current), display=name)
                return
            if bang_tokens[0] == "cd":
                yield from self._yield_local_dir_completions(bang_current)
            elif bang_tokens[0] in {"ls", "dir"}:
                yield from self._yield_local_path_completions(bang_current)
            return

        if not tokens:
            yield from self._yield_local_dir_completions(current)
            yield from self._yield_command_completions(current)
            return

        if tokens[0] == "lcd":
            yield from self._yield_local_dir_completions(current)
            return
        if tokens[0] == "lls":
            yield from self._yield_local_path_completions(current)
            return
        if tokens[0] == "upload":
            yield from self._yield_local_path_completions(current)


class PanShell:
    def __init__(self, state: AppState) -> None:
        self.state = state
        self.console = create_console(state.ui, force_terminal=True)
        self.remote_cwd = "/"
        self.local_cwd = str(Path.cwd())
        self.home_root = state.session.home_path if state.session is not None else "/"
        self.manager: AsyncApiManager | None = None
        self.completer = LocalPathCompleter(self)

    async def login(self) -> None:
        self.state.interactive = True
        self.manager, self.home_root = await _login(self.console, state=self.state)
        self.remote_cwd = self.home_root

    async def close(self) -> None:
        self.state.interactive = False
        if self.state.session_controller is not None:
            await self.state.session_controller.close(state=self.state)

    def _set_env(self) -> tuple[str | None, str | None]:
        previous_remote = os.environ.get(ENV_REMOTE_CWD) or os.environ.get(LEGACY_ENV_REMOTE_CWD)
        previous_local = os.environ.get(ENV_LOCAL_CWD) or os.environ.get(LEGACY_ENV_LOCAL_CWD)
        os.environ[ENV_REMOTE_CWD] = self.remote_cwd
        os.environ[ENV_LOCAL_CWD] = self.local_cwd
        os.environ[LEGACY_ENV_REMOTE_CWD] = self.remote_cwd
        os.environ[LEGACY_ENV_LOCAL_CWD] = self.local_cwd
        return previous_remote, previous_local

    def _restore_env(self, previous_remote: str | None, previous_local: str | None) -> None:
        for key in (ENV_REMOTE_CWD, LEGACY_ENV_REMOTE_CWD):
            if previous_remote is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = previous_remote
        for key in (ENV_LOCAL_CWD, LEGACY_ENV_LOCAL_CWD):
            if previous_local is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = previous_local

    async def run(self, *, login: bool = True) -> None:
        if login:
            if self.state.runtime_context.shared_environment:
                self.console.print("当前为共享环境的临时私有会话；退出 shell 后登录状态即销毁。")
            await self.login()
        elif self.state.session is not None:
            self.state.interactive = True
            self.manager = self.state.session.manager
            self.home_root = self.state.session.home_path
            self.remote_cwd = self.home_root
        else:
            raise RuntimeError("进入 shell 前没有可用 session")
        session = PromptSession(history=InMemoryHistory(), completer=self.completer, complete_while_typing=True)
        try:
            while True:
                try:
                    text = await session.prompt_async(f"pansh [{self.remote_cwd}] $ ")
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

    def _render_help_section(self, title: str, rows: list[tuple[str, str]]) -> Group:
        table = Table.grid(padding=(0, 2), expand=False)
        table.add_column(style="accent", no_wrap=True)
        table.add_column(style="text")
        for command, description in rows:
            table.add_row(command, description)
        return Group(Text(title, style="accent"), table)

    def _print_help(self) -> None:
        body = Group(
            self._render_help_section("Shell builtins", INTERACTIVE_COMMANDS),
            Text("这些命令用于控制 shell 本身，以及切换本地 / 远端工作目录。", style="muted"),
            Text(""),
            self._render_help_section("Stable commands", VISIBLE_COMMANDS),
            Text("这里只展示当前正式对外支持的命令。", style="muted"),
            Text(""),
            Text("提示", style="accent"),
            Text("  用 '<command> --help' 查看完整参数说明。", style="muted"),
            Text("  在 shell 里，'<command> -h' 会自动等价为 '--help'。", style="muted"),
            Text("  首字符输入时会同时提示本地目录与可用命令；Tab 可补全本地路径。", style="muted"),
            Text("  `logout` 会注销当前会话并退出；`exit` / `quit` 只退出。", style="muted"),
        )
        self.console.print(Panel.fit(body, title="pansh Shell", border_style="text", padding=(0, 1)))

    async def _show_command_help(self, command_name: str) -> None:
        previous_remote, previous_local = self._set_env()
        try:
            await asyncio.to_thread(
                get_command(app).main,
                args=[command_name, "--help"],
                prog_name="pansh",
                standalone_mode=False,
                obj=self.state,
            )
        except click.ClickException as exc:
            self.console.print(f"命令错误：{exc.format_message()}", style="error")
        except (typer.Exit, SystemExit):
            pass
        finally:
            self._restore_env(previous_remote, previous_local)

    def _resolve_remote_path(self, target: str) -> str:
        previous_remote, previous_local = self._set_env()
        try:
            candidate = _normalize_remote_path(target, self.home_root)
        finally:
            self._restore_env(previous_remote, previous_local)
        if candidate == "/":
            return self.home_root
        return candidate

    def _resolve_local_path(self, target: str | None = None) -> Path:
        raw = target or self.local_cwd
        candidate = Path(raw).expanduser()
        if not candidate.is_absolute():
            candidate = Path(self.local_cwd) / candidate
        return candidate.resolve()

    async def handle(self, text: str) -> bool:
        if text in {"exit", "quit"}:
            return True
        if text in {"help", "?"}:
            self._print_help()
            return False
        if text in {"clear", "cls"}:
            self.console.clear()
            return False
        if text.startswith("!"):
            bang = text[1:].strip()
            if not bang:
                return False
            try:
                bang_argv = shlex.split(bang)
            except ValueError as exc:
                self.console.print(f"命令解析失败：{exc}", style="error")
                return False
            if bang_argv and bang_argv[0] == "cd":
                target = bang_argv[1] if len(bang_argv) > 1 else "."
                resolved = self._resolve_local_path(target)
                if not resolved.exists() or not resolved.is_dir():
                    self.console.print(f"不是目录：{resolved}", style="error")
                    return False
                self.local_cwd = str(resolved)
                return False
            completed = subprocess.run(bang, cwd=self.local_cwd, shell=True)
            if completed.returncode != 0:
                self.console.print(f"本地命令执行失败，退出码 {completed.returncode}", style="error")
            return False
        try:
            argv = shlex.split(text)
        except ValueError as exc:
            self.console.print(f"命令解析失败：{exc}", style="error")
            return False
        if not argv:
            return False
        if argv[0] in {"help", "?"}:
            if len(argv) == 1:
                self._print_help()
            else:
                await self._show_command_help(argv[1])
            return False
        if argv[0] == "logout":
            if self.state.session_controller is not None:
                session = self.state.session
                await self.state.session_controller.logout(state=self.state)
                if session is not None and session.mode == "persistent":
                    self.console.print("已注销并删除本地凭据。")
                else:
                    self.console.print("已结束临时会话。")
            return True
        if argv[0] == "pwd":
            self.console.print(self.remote_cwd)
            return False
        if argv[0] == "cd":
            target = argv[1] if len(argv) > 1 else "."
            candidate = self._resolve_remote_path(target)
            info = await self.manager.get_resource_info_by_path(candidate.strip("/")) if self.manager else None
            if info is None or not info.is_dir:
                self.console.print(f"不是目录：{candidate}", style="error")
                return False
            self.remote_cwd = candidate
            return False
        if argv[0] == "lpwd":
            self.console.print(self.local_cwd)
            return False
        if argv[0] == "lcd":
            target = self._resolve_local_path(argv[1] if len(argv) > 1 else self.local_cwd)
            if not target.exists() or not target.is_dir():
                self.console.print(f"不是目录：{target}", style="error")
                return False
            self.local_cwd = str(target)
            return False
        if argv[0] == "lls":
            target = self._resolve_local_path(argv[1] if len(argv) > 1 else self.local_cwd)
            if not target.exists():
                self.console.print(f"路径不存在：{target}", style="error")
                return False
            for item in sorted(target.iterdir(), key=lambda path: (not path.is_dir(), path.name.lower())):
                suffix = "/" if item.is_dir() else ""
                self.console.print(item.name + suffix)
            return False

        previous_remote, previous_local = self._set_env()
        try:
            if len(argv) == 2 and argv[1] == "-h":
                argv = [argv[0], "--help"]
            await asyncio.to_thread(
                get_command(app).main,
                args=argv,
                prog_name="pansh",
                standalone_mode=False,
                obj=self.state,
            )
        except click.ClickException as exc:
            self.console.print(f"命令错误：{exc.format_message()}", style="error")
        except click.Abort:
            self.console.print("操作已中止。", style="warning")
        except typer.Exit as exc:
            if exc.exit_code not in (None, 0):
                self.console.print(f"命令执行失败，退出码 {exc.exit_code}", style="error")
        except SystemExit as exc:
            if exc.code not in (None, 0):
                self.console.print(f"命令执行失败，退出码 {exc.code}", style="error")
        except Exception as exc:
            self.console.print(f"发生未预期错误：{exc}", style="error")
        finally:
            self._restore_env(previous_remote, previous_local)
        return False


def run_interactive_shell(state: AppState) -> None:
    try:
        asyncio.run(run_shell_with_state(state))
    except KeyboardInterrupt:
        pass


async def run_shell_with_state(state: AppState, *, login: bool = True) -> None:
    await PanShell(state).run(login=login)
