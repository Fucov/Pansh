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
from prompt_toolkit.history import InMemoryHistory
from rich.console import Group
from rich.panel import Panel
from rich.table import Table
from rich.text import Text
from typer.main import get_command

from .api import AsyncApiManager
from .main import _login, _normalize_remote_path, app
from .theme import UIOptions, create_console

INTERACTIVE_COMMANDS = [
    ("help [command]", "显示 shell 帮助或指定 command 的帮助。"),
    ("clear, cls", "清空终端显示。"),
    ("exit, quit", "退出交互式 shell。"),
    ("pwd", "显示当前远端目录。"),
    ("cd [路径]", "切换远端工作目录。"),
    ("lpwd", "显示当前本地目录。"),
    ("lcd [路径]", "切换本地工作目录。"),
    ("lls [路径]", "列出本地文件。"),
    ("!<command>", "执行本地 shell command。"),
]

REMOTE_COMMANDS = [
    ("ls [路径]", "列出远端目录内容。"),
    ("tree [路径]", "以树形方式显示远端目录。"),
    ("stat <路径>", "查看文件或目录的详细信息。"),
    ("find <keyword>", "在远端路径下按 keyword 查找。"),
    ("quota", "查看空间配额使用情况。"),
    ("mkdir <路径>", "创建远端目录。"),
    ("touch <路径>", "创建空文件。"),
    ("rm <路径>", "删除远端文件或目录。"),
    ("mv <源> <目标>", "移动或重命名远端项目。"),
    ("cp <源> <目标>", "复制远端项目。"),
    ("cat <路径>", "输出远端文本文件内容。"),
    ("link ...", "管理分享链接。"),
    ("upload ...", "上传文件；默认目标为当前远端目录。"),
    ("download ...", "下载文件；默认目标为当前本地目录。"),
    ("revisions <路径>", "列出文件历史版本。"),
    ("restore-revision", "恢复指定历史版本。"),
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
        previous_remote = os.environ.get("PANCLI_REMOTE_CWD")
        previous_local = os.environ.get("PANCLI_LOCAL_CWD")
        os.environ["PANCLI_REMOTE_CWD"] = self.remote_cwd
        os.environ["PANCLI_LOCAL_CWD"] = self.local_cwd
        try:
            await asyncio.to_thread(
                get_command(app).main,
                args=[command_name, "--help"],
                prog_name="pancli",
                standalone_mode=False,
            )
        except click.ClickException as exc:
            self.console.print(f"命令错误：{exc.format_message()}", style="error")
        except typer.Exit:
            pass
        except SystemExit:
            pass
        finally:
            if previous_remote is None:
                os.environ.pop("PANCLI_REMOTE_CWD", None)
            else:
                os.environ["PANCLI_REMOTE_CWD"] = previous_remote
            if previous_local is None:
                os.environ.pop("PANCLI_LOCAL_CWD", None)
            else:
                os.environ["PANCLI_LOCAL_CWD"] = previous_local

    def _render_help_section(self, title: str, rows: list[tuple[str, str]]) -> Group:
        table = Table.grid(padding=(0, 2), expand=False)
        table.add_column(style="accent", no_wrap=True)
        table.add_column(style="text")
        for command, description in rows:
            table.add_row(command, description)
        return Group(Text(title, style="accent"), table)

    def _print_help(self) -> None:
        body = Group(
            self._render_help_section("交互辅助命令", INTERACTIVE_COMMANDS),
            Text("这些命令用于控制 shell 本身以及本地目录切换。", style="muted"),
            Text(""),
            self._render_help_section("远端文件命令", REMOTE_COMMANDS),
            Text("这些命令用于操作 AnyShare 远端文件和目录。", style="muted"),
            Text(""),
            Text("提示", style="accent"),
            Text("  使用 '<command> --help' 查看完整参数说明。", style="muted"),
            Text("  在 shell 中，'<command> -h' 会自动当作帮助处理。", style="muted"),
        )
        self.console.print(
            Panel.fit(
                body,
                title="PanCLI Shell",
                border_style="text",
                padding=(0, 1),
            )
        )

    def _resolve_remote_path(self, target: str) -> str:
        previous_remote = os.environ.get("PANCLI_REMOTE_CWD")
        previous_local = os.environ.get("PANCLI_LOCAL_CWD")
        os.environ["PANCLI_REMOTE_CWD"] = self.remote_cwd
        os.environ["PANCLI_LOCAL_CWD"] = self.local_cwd
        try:
            candidate = _normalize_remote_path(target, self.home_root)
        finally:
            if previous_remote is None:
                os.environ.pop("PANCLI_REMOTE_CWD", None)
            else:
                os.environ["PANCLI_REMOTE_CWD"] = previous_remote
            if previous_local is None:
                os.environ.pop("PANCLI_LOCAL_CWD", None)
            else:
                os.environ["PANCLI_LOCAL_CWD"] = previous_local
        if candidate == "/":
            return self.home_root
        return candidate

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
            completed = subprocess.run(text[1:], cwd=self.local_cwd, shell=True)
            if completed.returncode != 0:
                self.console.print(f"本地命令执行失败：{completed.returncode}")
            return False
        try:
            argv = shlex.split(text)
        except ValueError as exc:
            self.console.print(f"解析命令失败：{exc}", style="error")
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
            candidate = self._resolve_remote_path(target)
            info = await self.manager.get_resource_info_by_path(candidate.strip("/")) if self.manager else None
            if info is None or not info.is_dir:
                self.console.print(f"不是目录：{candidate}")
                return False
            self.remote_cwd = candidate
            return False
        if cmd == "lpwd":
            self.console.print(self.local_cwd)
            return False
        if cmd == "lcd":
            target = Path(argv[1] if len(argv) > 1 else self.local_cwd).expanduser().resolve()
            if not target.exists() or not target.is_dir():
                self.console.print(f"不是目录：{target}")
                return False
            self.local_cwd = str(target)
            return False
        if cmd == "lls":
            target = Path(argv[1] if len(argv) > 1 else self.local_cwd).expanduser().resolve()
            if not target.exists():
                self.console.print(f"路径不存在：{target}")
                return False
            for item in sorted(target.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower())):
                suffix = "/" if item.is_dir() else ""
                self.console.print(item.name + suffix)
            return False

        previous_remote = os.environ.get("PANCLI_REMOTE_CWD")
        previous_local = os.environ.get("PANCLI_LOCAL_CWD")
        os.environ["PANCLI_REMOTE_CWD"] = self.remote_cwd
        os.environ["PANCLI_LOCAL_CWD"] = self.local_cwd
        try:
            if len(argv) == 2 and argv[1] == "-h":
                argv = [argv[0], "--help"]
            await asyncio.to_thread(
                get_command(app).main,
                args=argv,
                prog_name="pancli",
                standalone_mode=False,
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
            if previous_remote is None:
                os.environ.pop("PANCLI_REMOTE_CWD", None)
            else:
                os.environ["PANCLI_REMOTE_CWD"] = previous_remote
            if previous_local is None:
                os.environ.pop("PANCLI_LOCAL_CWD", None)
            else:
                os.environ["PANCLI_LOCAL_CWD"] = previous_local
        return False


def run_interactive_shell(ui: UIOptions | None = None) -> None:
    try:
        asyncio.run(PanShell(ui).run())
    except KeyboardInterrupt:
        pass
