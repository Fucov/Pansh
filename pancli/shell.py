"""Stateful REPL Shell for AnyShare — PanCLI v3 全异步架构."""

from __future__ import annotations

import argparse
import asyncio
import collections
import fnmatch
import glob as glob_mod
import getpass
import os
import re
import shlex
import subprocess
import sys
import time
from pathlib import Path

from prompt_toolkit import PromptSession
from prompt_toolkit.completion import Completer, Completion, ThreadedCompleter
from prompt_toolkit.document import Document
from prompt_toolkit.history import InMemoryHistory
from rich.console import Console
from rich.panel import Panel
from rich.progress import (
    BarColumn,
    DownloadColumn,
    Progress,
    SpinnerColumn,
    TaskID,
    TextColumn,
    TimeElapsedColumn,
    TimeRemainingColumn,
    TransferSpeedColumn,
)
from rich.style import Style
from rich.table import Table
from rich.text import Text
from rich.tree import Tree

from .api import (
    AsyncApiManager,
    InvalidRootException,
    WrongPasswordException,
)
from .auth import rsa_encrypt
from .config import load_config, save_config
from .models import AppConfig, TransferTask
from .settings import get_styles, init_settings, load_settings


# ═══════════════════════════════════════════════════════════════════════════════
# 全局 Console — 延迟创建，按运行时 theme 配置
# ═══════════════════════════════════════════════════════════════════════════════

_console_holder: list[Console] = []


def _init_console() -> Console:
    _console_holder.clear()
    _console_holder.append(Console(force_terminal=True, markup=True))
    return _console_holder[0]


console: Console = _init_console()


# ═══════════════════════════════════════════════════════════════════════════════
# 样式辅助 — S 对象按需从 settings 读取当前主题样式
# ═══════════════════════════════════════════════════════════════════════════════

class _S:
    """按需读取当前主题样式的代理对象，支持 .属性 和 ["属性"] 两种访问。"""
    __slots__ = ("_cache",)

    def __init__(self) -> None:
        self._cache: dict[str, str] = {}

    def _refresh(self, dark: bool) -> None:
        self._cache.clear()
        raw = get_styles(dark)
        for key, style in raw.items():
            tag = str(style).strip()
            if tag and tag != "none":
                self._cache[key] = f"[{tag}]"
            else:
                self._cache[key] = "[/]"

    def __getattr__(self, key: str) -> str:
        if not self._cache:
            s = load_settings()
            self._refresh(s.is_dark)
        return self._cache.get(key, "")


S = _S()


def _style_tag(key: str) -> str:
    """返回指定语义色的 Rich markup 字符串。"""
    return getattr(S, key)


# ═══════════════════════════════════════════════════════════════════════════════
# 辅助函数
# ═══════════════════════════════════════════════════════════════════════════════


def _sizeof_fmt(num: float, suffix: str = "") -> str:
    for unit in ("", "K", "M", "G", "T", "P", "E", "Z"):
        if abs(num) < 1024.0:
            return f"{num:3.1f}{unit}{suffix}"
        num /= 1024.0
    return f"{num:.1f}Yi{suffix}"


def _ts_fmt(us: int) -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(us / 1_000_000))


def _prompt_path(cwd: str) -> str:
    return f"PanCLI [{cwd}] $ "


def _ask_confirm(prompt: str) -> bool:
    raw = console.input(f"[yellow]{prompt} [y/N]: [/yellow]")
    return raw.strip().lower() in ("y", "yes", "是")


def _panel(title: str, border: str, *rows: tuple[str, str]) -> None:
    t = Table(box=None, show_header=False)
    t.add_column("cmd", style="cyan")
    t.add_column("desc", style="white")
    for cmd, desc in rows:
        t.add_row(cmd, desc)
    console.print(Panel(t, title=title, border_style=border))


def _tbl(*columns: tuple[str, str, str | None, str]) -> Table:
    """构建统一外观的 Table。返回后可 add_row。"""
    t = Table(show_header=True, border_style=load_settings().table_border)
    for hdr, style_key, width, justify in columns:
        kw: dict = {"style": _style_tag(style_key)}
        if width:
            kw["width"] = int(width)
        if justify:
            kw["justify"] = justify
        t.add_column(hdr, **kw)
    return t


def _refresh_styles() -> None:
    """重新加载样式（theme 切换后调用，清空缓存后按当前主题重建）。"""
    S._cache.clear()
    S._refresh(load_settings().is_dark)


# ═══════════════════════════════════════════════════════════════════════════════
# Completer
# ═══════════════════════════════════════════════════════════════════════════════


class AnyShareCompleter(Completer):
    def __init__(self, shell: "PanShell"):
        self.shell = shell
        self.cmds = [
            "ls", "cd", "pwd", "tree", "cat", "head", "tail", "touch",
            "stat", "mkdir", "rm", "mv", "cp", "upload", "download",
            "find", "link", "whoami", "logout", "su", "clear", "exit",
            "quit", "help", "config", "lls", "lcd", "lpwd",
        ]
        self._cache: dict[str, list[str]] = {}
        self._path_cache: dict[str, str] = {}

    def _get_info(self, path: str):
        try:
            import asyncio as _aio
            return _aio.run(self.shell.manager.get_resource_info_by_path(path))
        except Exception:
            return None

    def _list_dir_sync(self, docid: str):
        try:
            import asyncio as _aio
            return _aio.run(self.shell.manager.list_dir(docid, by="name"))
        except Exception:
            return [], []

    def get_completions(self, document: Document, complete_event):
        text = document.text_before_cursor
        try:
            args = shlex.split(text)
        except ValueError:
            return

        if not text or (len(args) == 1 and not text.endswith(" ")):
            word = args[0] if args else ""
            for cmd in self.cmds:
                if cmd.startswith(word):
                    yield Completion(cmd, start_position=-len(word))
            return

        word = "" if text.endswith(" ") else args[-1]
        cmd = args[0]

        if cmd in ("upload", "lls", "lcd", "!") and (
            len(args) == 2 if not text.endswith(" ") else len(args) == 1
        ):
            base = word
            if not os.path.isabs(base) and not base.startswith("."):
                base = os.path.join(self.shell._local_cwd, base)
            parent_dir = os.path.dirname(base)
            prefix = os.path.basename(base)
            try:
                entries = os.listdir(parent_dir) if os.path.isdir(parent_dir) else []
            except OSError:
                entries = []
            for entry in entries:
                if entry.startswith(prefix):
                    full = os.path.join(parent_dir, entry)
                    is_dir = os.path.isdir(full)
                    icon = "[blue]📁[/blue]" if is_dir else "[white]📄[/white]"
                    display = entry + ("/" if is_dir else "")
                    yield Completion(entry + ("/" if is_dir else ""),
                                     start_position=-len(word), display=display)
            return

        if "/" not in word:
            return

        parts = word.rsplit("/", 1)
        parent, prefix = parts[0] or "/", parts[1]

        if not self.shell.manager:
            return

        try:
            if parent == "/":
                entrydoc = self.shell._cached_entrydoc
                if entrydoc:
                    for root in entrydoc:
                        if root["name"].startswith(prefix):
                            yield Completion("/" + root["name"] + "/", start_position=-len(word))
                return

            abs_parent = self.shell.abs_path(parent)
            docid = self._path_cache.get(abs_parent)
            if not docid:
                info = self._get_info(abs_parent.strip("/"))
                if info and info.size == -1:
                    docid = info.docid
                    self._path_cache[abs_parent] = docid

            if docid:
                if docid not in self._cache:
                    dirs, files = self._list_dir_sync(docid)
                    self._cache[docid] = [d.name + "/" for d in dirs] + [f.name for f in files]
                for name in self._cache[docid]:
                    if name.startswith(prefix):
                        yield Completion(name, start_position=-len(prefix))
        except Exception:
            pass


# ═══════════════════════════════════════════════════════════════════════════════
# PanShell
# ═══════════════════════════════════════════════════════════════════════════════


class PanShell:
    def __init__(self) -> None:
        self.cfg: AppConfig = load_config()
        self.settings = init_settings()
        self.manager: AsyncApiManager | None = None
        self.cwd: str = "/"
        self.home_name: str = ""
        self._cached_entrydoc: list[dict] = []
        self._local_cwd: str = os.getcwd()
        _refresh_styles()

    async def login(self) -> None:
        if self.cfg.username:
            username = self.cfg.username
        else:
            username = console.input(f"{S.info}Username:{S.dim} ")
            self.cfg.username = username

        store_password = self.cfg.store_password
        password: str | None = None
        encrypted: str | None = None

        if store_password:
            encrypted = self.cfg.encrypted
            if encrypted is None:
                password = getpass.getpass()
                encrypted = rsa_encrypt(password, self.cfg.pubkey)
                self.cfg.encrypted = encrypted
        else:
            password = getpass.getpass()
            encrypted = rsa_encrypt(password, self.cfg.pubkey)

        for retry in range(3):
            try:
                self.manager = AsyncApiManager(
                    self.cfg.host, username, password, self.cfg.pubkey,
                    encrypted=encrypted,
                    cached_token=self.cfg.cached_token.token or None,
                    cached_expire=self.cfg.cached_token.expires or None,
                )
                await self.manager.initialize()
                break
            except WrongPasswordException:
                console.print(f"{S.warning}密码错误重试 ({retry + 1}/3){S.dim}")
                time.sleep(1)
                password = getpass.getpass()
                encrypted = rsa_encrypt(password, self.cfg.pubkey)
                self.cfg.encrypted = encrypted

        if self.manager is None:
            self.cfg.username = None
            self.cfg.encrypted = None
            save_config(self.cfg)
            console.print(f"{S.error}登录失败{S.dim}")
            sys.exit(1)

        if self.manager._expires > 0:
            self.cfg.cached_token.token = self.manager._tokenid
            self.cfg.cached_token.expires = self.manager._expires
        save_config(self.cfg)
        console.print(f"{S.success}✓{S.dim} 已连接: {self.cfg.host}")

        self._cached_entrydoc = await self.manager.get_entrydoc()
        if not self._cached_entrydoc:
            console.print(f"{S.error}无法获取文档库根目录{S.dim}")
            sys.exit(1)
        self.home_name = self._cached_entrydoc[0]["name"]
        self.cwd = f"/{self.home_name}"

    def abs_path(self, path: str) -> str:
        if path.startswith("/"):
            p = path
        else:
            p = f"{self.cwd}/{path}" if self.cwd != "/" else f"/{path}"
        parts = []
        for part in p.split("/"):
            if not part or part == ".":
                continue
            if part == "..":
                if parts:
                    parts.pop()
            else:
                parts.append(part)
        return "/" + "/".join(parts) if parts else "/"

    async def run_async(self) -> None:
        await self.login()
        session = PromptSession(
            history=InMemoryHistory(),
            completer=ThreadedCompleter(AnyShareCompleter(self)),
        )

        try:
            while True:
                try:
                    text = await session.prompt_async(_prompt_path(self.cwd))
                    if not text.strip():
                        continue
                    await self.execute_command(text)
                except KeyboardInterrupt:
                    console.print(f"\n{S.dim}(Ctrl+C){S.dim}")
                    continue
                except EOFError:
                    break
        except Exception as e:
            console.print(f"{S.error}Error:{S.dim} {e}")
        finally:
            if self.manager:
                await self.manager.close()

    async def execute_command(self, text: str) -> None:
        try:
            args = shlex.split(text)
        except ValueError:
            console.print(f"{S.warning}命令解析错误{S.dim}")
            return
        if not args:
            return
        cmd = args[0]

        if cmd.startswith("!"):
            full_cmd = cmd[1:]
            if args[1:]:
                parts = [full_cmd] + [shlex.quote(a) for a in args[1:]]
                full_cmd = " ".join(parts)
            await self.cmd_shell(full_cmd)
            return

        handler = getattr(self, f"cmd_{cmd}", None)
        if handler is None:
            await self.cmd_unknown(args[1:])
            return
        try:
            await handler(args[1:])
        except TypeError as e:
            console.print(f"{S.error}命令执行错误:{S.dim} {e}")

    # ─────────────────────────────────────────────────────────────────────
    # 命令处理器
    # ─────────────────────────────────────────────────────────────────────

    async def cmd_unknown(self, args: list[str]) -> None:
        console.print(f"{S.warning}Unknown command. Type 'help'.{S.dim}")

    async def cmd_help(self, args: list[str]) -> None:
        console.print(f"\n{S.title}PanCLI 命令参考手册 v3{S.dim}\n")
        _panel("[cyan]环境与基础[/cyan]", "cyan",
            ("whoami", "查账户"),
            ("su [user]", "切账号"),
            ("logout", "清凭证"),
            ("config [show/get/set]", "查看/修改配置"),
            ("clear", "清屏"),
            ("exit/quit", "退出"),
        )
        _panel("[green]导航与属性[/green]", "green",
            ("ls [dir] [-h]", "列表"),
            ("cd <dir>", "切换目录"),
            ("pwd", "显示当前路径"),
            ("tree [dir]", "树状图"),
            ("stat <path>", "查元数据"),
            ("find <keyword>", "搜索文件"),
        )
        _panel("[yellow]文件管理[/yellow]", "yellow",
            ("cat <file>", "打印全部"),
            ("head/tail <file>", "读头/尾部"),
            ("touch/mkdir <path>", "建文件/目录"),
            ("rm <path> [-r]", "删除"),
            ("mv/cp <src> <dst>", "移动/复制"),
        )
        _panel("[magenta]传输管理[/magenta]", "magenta",
            ("upload <本地> [远程] [-y]", "上传（支持通配符，确认后执行）"),
            ("download <远程> [本地] [-y]", "下载（支持通配符，确认后执行）"),
        )
        _panel("[blue]本地 Shell[/blue]", "blue",
            ("!<cmd> [args...]", "执行本地 Shell，参数完整透传"),
            ("lls [path] [-lh]", "列本地目录"),
            ("lcd <path>", "切本地目录"),
            ("lpwd", "本地工作目录"),
        )

    async def cmd_exit(self, args: list[str]) -> None:
        raise EOFError
    async def cmd_quit(self, args: list[str]) -> None:
        raise EOFError
    async def cmd_clear(self, args: list[str]) -> None:
        console.clear()
    async def cmd_pwd(self, args: list[str]) -> None:
        console.print(self.cwd)

    async def cmd_whoami(self, args: list[str]) -> None:
        console.print(f"{S.info}当前用户:{S.dim} {self.cfg.username}")
        console.print(f"{S.info}网盘 Host:{S.dim} {self.cfg.host}")
        status = f"{S.success}已在本地保存密码{S.dim}" if self.cfg.encrypted else f"{S.warning}未在本地保存密码{S.dim}"
        console.print(f"{S.info}凭据状态:{S.dim} {status}")

    async def cmd_logout(self, args: list[str]) -> None:
        self.cfg.username = None
        self.cfg.encrypted = None
        self.cfg.cached_token.token = ""
        save_config(self.cfg)
        console.print(f"{S.success}✓{S.dim} 已清除本地凭据。退出当前 Shell...")
        raise EOFError

    async def cmd_su(self, args: list[str]) -> None:
        self.cfg.username = args[0] if args else None
        self.cfg.encrypted = None
        self.cfg.cached_token.token = ""
        save_config(self.cfg)
        console.print(f"{S.info}准备切换账号，按要求重新登录...{S.dim}")
        if self.manager:
            await self.manager.close()
        self._cached_entrydoc = []
        await self.login()

    # ── config ─────────────────────────────────────────────────────────
    async def cmd_config(self, args: list[str]) -> None:
        if not args:
            await self._show_config()
            return
        action = args[0]
        if action == "show":
            await self._show_config()
        elif action == "get" and len(args) > 1:
            val = self.settings.get(args[1])
            console.print(f"{S.info}{args[1]}:{S.dim} {val}")
        elif action == "set" and len(args) > 2:
            key, val = args[1], args[2]
            try:
                if val.isdigit():
                    val = int(val)
                elif val.lower() in ("true", "false"):
                    val = val.lower() == "true"
            except ValueError:
                pass
            self.settings.set(key, val)
            self.settings.save()
            console.print(f"{S.success}✓{S.dim} {key} = {val}")
            if key == "theme":
                _refresh_styles()
                console.print(f"{S.info}主题已切换，重新执行命令查看效果{S.dim}")
        elif action == "reload":
            self.settings = self.settings.reload()
            _refresh_styles()
            console.print(f"{S.success}✓{S.dim} 配置已重新加载")
        else:
            console.print(f"{S.warning}用法: config [show|get <key>|set <key> <val>|reload]{S.dim}")

    async def _show_config(self) -> None:
        t = _tbl(
            ("Key", "info", "20", None),
            ("Value", "file", "30", None),
        )
        for key in ("theme", "transfer.default_jobs", "transfer.chunk_size",
                    "search.default_depth", "search.max_depth"):
            val = self.settings.get(key)
            t.add_row(key, str(val))
        console.print(t)
        console.print(f"\n{S.dim}配置文件: {self.settings._path}{S.dim}")

    # ── 本地文件系统 ──────────────────────────────────────────────────
    async def cmd_lls(self, args: list[str]) -> None:
        parser = argparse.ArgumentParser(prog="lls", add_help=False)
        parser.add_argument("path", nargs="?", default=".")
        parser.add_argument("-l", action="store_true")
        parser.add_argument("-h", "--human", action="store_true")
        try:
            parsed = parser.parse_args(args)
        except SystemExit:
            return
        target = os.path.join(self._local_cwd, parsed.path)
        if not os.path.exists(target):
            console.print(f"{S.error}本地路径不存在:{S.dim} {target}")
            return
        if os.path.isfile(target):
            console.print(target)
            return
        try:
            entries = sorted(os.listdir(target))
        except PermissionError:
            console.print(f"{S.error}权限不足{S.dim}")
            return
        if parsed.l or parsed.human:
            for entry in entries:
                full = os.path.join(target, entry)
                try:
                    st = os.stat(full)
                    sz = _sizeof_fmt(st.st_size) if parsed.human else str(st.st_size)
                    d = "d" if os.path.isdir(full) else "-"
                    console.print(f"{d}  {sz:>10}  {entry}")
                except Exception:
                    console.print(f"?  {'?':>10}  {entry}")
        else:
            for entry in entries:
                full = os.path.join(target, entry)
                is_dir = os.path.isdir(full)
                icon = f"{S.folder}📁{S.dim} " if is_dir else f"{S.file}📄{S.dim} "
                console.print(f"{icon}{entry}")

    async def cmd_lcd(self, args: list[str]) -> None:
        if not args:
            console.print(self._local_cwd)
            return
        target = os.path.join(self._local_cwd, args[0])
        if not os.path.isdir(target):
            console.print(f"{S.error}本地目录不存在:{S.dim} {target}")
            return
        self._local_cwd = os.path.abspath(target)
        console.print(f"{S.success}本地目录:{S.dim} {self._local_cwd}")

    async def cmd_lpwd(self, args: list[str]) -> None:
        console.print(self._local_cwd)

    # ── 本地 Shell ────────────────────────────────────────────────────
    async def cmd_shell(self, full_cmd: str) -> None:
        if not full_cmd:
            return
        try:
            proc = await asyncio.create_subprocess_shell(
                full_cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
                cwd=self._local_cwd,
            )
            stdout, _ = await proc.communicate()
            if stdout:
                text = stdout.decode("utf-8", errors="replace").strip()
                if text:
                    console.print(Text(text))
            if proc.returncode != 0 and proc.returncode is not None:
                console.print(f"{S.dim}退出码: {proc.returncode}{S.dim}")
        except Exception as e:
            console.print(f"{S.error}Shell 执行错误:{S.dim} {e}")

    # ── 云端文件操作 ──────────────────────────────────────────────────

    async def cmd_cd(self, args: list[str]) -> None:
        target = self.abs_path(args[0]) if args else f"/{self.home_name}"
        if target == "/":
            self.cwd = "/"
            return
        info = await self.manager.get_resource_info_by_path(target.strip("/"))
        if not info:
            console.print(f"{S.error}无此目录:{S.dim} {target}")
        elif info.size != -1:
            console.print(f"{S.error}非目录:{S.dim} {target}")
        else:
            self.cwd = target

    async def cmd_ls(self, args: list[str]) -> None:
        parser = argparse.ArgumentParser(prog="ls", add_help=False)
        parser.add_argument("path", nargs="?", default=".")
        parser.add_argument("-h", "--human", action="store_true")
        try:
            parsed = parser.parse_args(args)
        except SystemExit:
            return
        target = self.abs_path(parsed.path)
        if target == "/":
            for root in self._cached_entrydoc:
                console.print(f"{S.folder}📁{S.dim} {root['name']}")
            return
        info = await self.manager.get_resource_info_by_path(target.strip("/"))
        if not info:
            console.print(f"{S.error}不存在:{S.dim} {target}")
            return
        if info.size == -1:
            try:
                dirs, files = await self.manager.list_dir(info.docid, by="name")
            except Exception as e:
                console.print(f"{S.error}获取目录失败:{S.dim} {e}")
                return
            if not dirs and not files:
                console.print(f"{S.dim}空目录{S.dim}")
                return

            t = Table(
                title=f"{S.title}📂 {target}{S.dim}",
                show_header=True,
                border_style=self.settings.table_border,
                min_width=80,
            )
            t.add_column("类型", width=5, style="cyan")
            t.add_column("名称", style="bold", min_width=20)
            t.add_column("创建者", style="cyan", min_width=14)
            t.add_column("大小", justify="right", style="green", width=10)
            t.add_column("修改时间", style="yellow", width=19)

            for d in dirs:
                t.add_row(
                    f"{S.folder}📁{S.dim}",
                    f"{S.folder}{d.name}{S.dim}",
                    d.creator or "",
                    "",
                    _ts_fmt(d.modified),
                )
            for f in files:
                sz = _sizeof_fmt(f.size) if parsed.human else str(f.size)
                t.add_row(
                    f"{S.file}📄{S.dim}",
                    f"{S.file}{f.name}{S.dim}",
                    f.creator or "",
                    sz,
                    _ts_fmt(f.modified),
                )
            console.print(t)
        else:
            await self.cmd_stat([target])

    async def cmd_stat(self, args: list[str]) -> None:
        if not args:
            return
        target = self.abs_path(args[0])
        info = await self.manager.get_resource_info_by_path(target.strip("/"))
        if not info:
            console.print(f"{S.error}不存在:{S.dim} {target}")
            return
        meta = await self.manager.get_file_meta(info.docid)
        t = Table(title=f"📄 {target}", show_header=False, border_style=self.settings.table_border, box=None)
        t.add_column(f"{S.info}Key{S.dim}", style="bold")
        t.add_column("Value")
        t.add_row("DocID", meta.docid)
        t.add_row("大小", _sizeof_fmt(meta.size))
        t.add_row("类型", "目录" if info.size == -1 else "文件")
        t.add_row("修改时间", _ts_fmt(meta.modified))
        t.add_row("编辑者", meta.editor or "—")
        t.add_row("标签", ", ".join(meta.tags) if meta.tags else "—")
        console.print(t)

    async def cmd_tree(self, args: list[str]) -> None:
        target = self.abs_path(args[0] if args else ".")
        info = await self.manager.get_resource_info_by_path(target.strip("/"))
        if not info or info.size != -1:
            console.print(f"{S.error}无效目录{S.dim}")
            return

        async def build(docid: str, node: Tree, depth: int = 0) -> None:
            if depth > 10:
                return
            try:
                dirs, files = await self.manager.list_dir(docid, by="name")
                for d in dirs:
                    sub = node.add(f"{S.folder}📁 {d.name}{S.dim}")
                    await build(d.docid, sub, depth + 1)
                for f in files:
                    node.add(f"{S.file}📄 {f.name}{S.dim} {S.dim}({_sizeof_fmt(f.size)}){S.dim}")
            except Exception:
                pass

        tree = Tree(f"{S.folder}📂 {target}{S.dim}")
        await build(info.docid, tree)
        console.print(tree)

    async def cmd_find(self, args: list[str]) -> None:
        parser = argparse.ArgumentParser(prog="find", add_help=False)
        parser.add_argument("keyword")
        parser.add_argument("-d", "--depth", type=int, default=None)
        try:
            parsed = parser.parse_args(args)
        except SystemExit:
            return
        depth = parsed.depth or self.settings.search_depth
        console.print(f"{S.info}🔍 搜索:{S.dim} {S.title}{parsed.keyword}{S.dim}")
        results = await self.manager.search(self.cwd, parsed.keyword, max_depth=depth)
        if not results:
            console.print(f"{S.warning}未找到匹配结果{S.dim}")
            return
        t = Table(
            title=f"{S.title}搜索结果 ({len(results)} 项){S.dim}",
            show_header=True, border_style=self.settings.table_border, min_width=80,
        )
        t.add_column("类型", width=5, style="cyan")
        t.add_column("名称", style="bold", min_width=20)
        t.add_column("路径", style="dim", min_width=25)
        t.add_column("大小", justify="right", style="green", width=10)
        t.add_column("修改时间", style="yellow", width=19)
        for r in results:
            icon = f"{S.folder}📁{S.dim}" if r.is_dir else f"{S.file}📄{S.dim}"
            name = f"{S.folder if r.is_dir else S.file}{r.name}{S.dim}"
            t.add_row(
                icon, name, r.path,
                _sizeof_fmt(r.size) if not r.is_dir else "—",
                _ts_fmt(r.modified),
            )
        console.print(t)

    async def cmd_mkdir(self, args: list[str]) -> None:
        if not args:
            return
        try:
            await self.manager.create_dirs_by_path(self.abs_path(args[0]).strip("/"))
            console.print(f"{S.success}✓{S.dim} 创建成功")
        except InvalidRootException:
            console.print(f"{S.error}无效根目录{S.dim}")

    async def cmd_rm(self, args: list[str]) -> None:
        parser = argparse.ArgumentParser(prog="rm", add_help=False)
        parser.add_argument("path")
        parser.add_argument("-r", "--recurse", action="store_true")
        try:
            parsed = parser.parse_args(args)
        except SystemExit:
            return
        target = self.abs_path(parsed.path).strip("/")
        info = await self.manager.get_resource_info_by_path(target)
        if not info:
            console.print(f"{S.error}不存在:{S.dim} {target}")
            return
        if info.size != -1:
            await self.manager.delete_file(info.docid)
        else:
            if not parsed.recurse:
                console.print(f"{S.warning}是目录，请加 -r{S.dim}")
                return
            await self.manager.delete_dir(info.docid)
        console.print(f"{S.success}✓{S.dim} 删除成功")

    async def cmd_cat(self, args: list[str]) -> None:
        if not args:
            return
        await self._print_file(args[0])

    async def cmd_head(self, args: list[str]) -> None:
        parser = argparse.ArgumentParser(prog="head", add_help=False)
        parser.add_argument("path", nargs="?")
        parser.add_argument("-n", "--lines", type=int, default=10)
        try:
            parsed = parser.parse_args(args)
        except SystemExit:
            return
        if not parsed.path:
            return
        target = self.abs_path(parsed.path).strip("/")
        info = await self.manager.get_resource_info_by_path(target)
        if not info or info.size == -1:
            return
        count = 0
        buf: list[bytes] = []
        try:
            async for chunk in self.manager.download_file_stream(info.docid):
                for line in chunk.split(b"\n"):
                    buf.append(line)
                    count += 1
                    if count >= parsed.lines:
                        break
                if count >= parsed.lines:
                    break
            for line in buf[: parsed.lines]:
                sys.stdout.buffer.write(line + b"\n")
        except BrokenPipeError:
            pass

    async def cmd_tail(self, args: list[str]) -> None:
        parser = argparse.ArgumentParser(prog="tail", add_help=False)
        parser.add_argument("path", nargs="?")
        parser.add_argument("-n", "--lines", type=int, default=10)
        try:
            parsed = parser.parse_args(args)
        except SystemExit:
            return
        if not parsed.path:
            return
        target = self.abs_path(parsed.path).strip("/")
        info = await self.manager.get_resource_info_by_path(target)
        if not info or info.size == -1:
            return
        window = collections.deque(maxlen=parsed.lines)
        buf = b""
        try:
            async for chunk in self.manager.download_file_stream(info.docid):
                buf += chunk
                while b"\n" in buf:
                    line, buf = buf.split(b"\n", 1)
                    window.append(line)
            if buf:
                window.append(buf)
            for line in window:
                sys.stdout.buffer.write(line + (b"" if line.endswith(b"\r") else b"\n"))
        except BrokenPipeError:
            pass

    async def _print_file(self, path: str, limit: int = -1) -> None:
        target = self.abs_path(path).strip("/")
        info = await self.manager.get_resource_info_by_path(target)
        if not info or info.size == -1:
            console.print(f"{S.error}文件无效{S.dim}")
            return
        read = 0
        try:
            async for chunk in self.manager.download_file_stream(info.docid):
                if limit > 0 and read + len(chunk) > limit:
                    sys.stdout.buffer.write(chunk[: limit - read])
                    break
                sys.stdout.buffer.write(chunk)
                read += len(chunk)
            sys.stdout.buffer.flush()
        except BrokenPipeError:
            pass
        print()

    async def cmd_touch(self, args: list[str]) -> None:
        if not args:
            return
        target = self.abs_path(args[0]).strip("/")
        parent = "/".join(target.split("/")[:-1]) or f"/{self.home_name}"
        name = target.split("/")[-1]
        pinfo = await self.manager.get_resource_info_by_path(parent.strip("/"))
        pdocid = pinfo.docid if pinfo else await self.manager.create_dirs_by_path(parent.strip("/"))
        await self.manager.upload_file(pdocid, name, b"")
        console.print(f"{S.success}✓{S.dim} 文件建立")

    async def cmd_mv(self, args: list[str]) -> None:
        await self._do_mv_cp(args, copy=False)

    async def cmd_cp(self, args: list[str]) -> None:
        await self._do_mv_cp(args, copy=True)

    async def _do_mv_cp(self, args: list[str], copy: bool) -> None:
        parser = argparse.ArgumentParser(add_help=False)
        parser.add_argument("src")
        parser.add_argument("dst")
        parser.add_argument("-f", "--force", action="store_true")
        try:
            p = parser.parse_args(args)
        except SystemExit:
            return
        src = self.abs_path(p.src)
        dst = self.abs_path(p.dst)
        action = "复制" if copy else "移动"
        src_parts = src.strip("/").split("/")
        dst_parts = dst.strip("/").split("/")
        if src_parts == dst_parts:
            console.print(f"{S.dim}无需操作{S.dim}")
            return
        src_info = await self.manager.get_resource_info_by_path(src.strip("/"))
        if src_info is None:
            console.print(f"{S.error}源路径不存在:{S.dim} {src}")
            return
        dst_info = await self.manager.get_resource_info_by_path(dst.strip("/"))
        if dst_info and dst_info.size == -1:
            if src_parts[:-1] == dst_parts:
                console.print(f"{S.dim}无需操作{S.dim}")
                return
            if src_parts == dst_parts[: len(src_parts)]:
                console.print(f"{S.error}不能移动到子目录{S.dim}")
                return
            if copy:
                await self.manager.copy_file(src_info.docid, dst_info.docid, overwrite_on_dup=p.force)
            else:
                await self.manager.move_file(src_info.docid, dst_info.docid, overwrite_on_dup=p.force)
            console.print(f"{S.success}✓{S.dim} {action}完成")
            return
        if dst_info is None:
            dst_name = dst_parts[-1]
            dst_parent = "/".join(dst_parts[:-1])
            dpi = await self.manager.get_resource_info_by_path(dst_parent)
            if dpi is None:
                console.print(f"{S.error}目标父目录不存在{S.dim}")
                return
            new_id, new_name = (
                await self.manager.copy_file(src_info.docid, dpi.docid, rename_on_dup=True)
                if copy else await self.manager.move_file(src_info.docid, dpi.docid, rename_on_dup=True)
            )
            if new_name != dst_name:
                await self.manager.rename_file(new_id, dst_name)
            console.print(f"{S.success}✓{S.dim} {action}完成: {src} → {dst}")
            return
        if src_info.size == -1:
            console.print(f"{S.error}不能将目录移动到文件位置{S.dim}")
            return
        if p.force:
            await self.manager.delete_file(dst_info.docid)
            dst_name = dst_parts[-1]
            dst_parent = "/".join(dst_parts[:-1])
            dpi = await self.manager.get_resource_info_by_path(dst_parent)
            if dpi:
                new_id, new_name = (
                    await self.manager.copy_file(src_info.docid, dpi.docid, rename_on_dup=True)
                    if copy else await self.manager.move_file(src_info.docid, dpi.docid, rename_on_dup=True)
                )
                if new_name != dst_name:
                    await self.manager.rename_file(new_id, dst_name)
            console.print(f"{S.success}✓{S.dim} {action}并覆盖完成")
        else:
            console.print(f"{S.warning}{dst} 已存在，使用 -f 覆盖{S.dim}")

    # ── 远程 glob ──────────────────────────────────────────────────────
    async def _glob_remote(
        self, base_docid: str, base_path: str, pattern: str
    ) -> list[TransferTask]:
        tasks: list[TransferTask] = []

        async def scan(docid: str, path: str, pat: str) -> None:
            dirs, files = await self.manager.list_dir(docid, by="name")
            for f in files:
                if fnmatch.fnmatch(f.name, pat):
                    full_path = f"{path}/{f.name}" if path != "/" else f"/{f.name}"
                    info = await self.manager.get_resource_info_by_path(full_path.strip("/"))
                    if info:
                        tasks.append(TransferTask(
                            remote_path=full_path,
                            local_path="",
                            size=info.size,
                            docid=info.docid,
                        ))
            for d in dirs:
                if fnmatch.fnmatch(d.name, pat):
                    full_path = f"{path}/{d.name}" if path != "/" else f"/{d.name}"
                    info = await self.manager.get_resource_info_by_path(full_path.strip("/"))
                    if info:
                        tasks.append(TransferTask(
                            remote_path=full_path,
                            local_path="",
                            size=info.size,
                            docid=info.docid,
                        ))

        await scan(base_docid, base_path, pattern)
        return tasks

    def _collect_local_files(self, pattern: str) -> list[tuple[str, int]]:
        files: list[tuple[str, int]] = []
        p = os.path.expanduser(pattern)
        if "*" in p or "?" in p or "[" in p:
            for m in glob_mod.glob(p):
                if os.path.isfile(m):
                    files.append((os.path.abspath(m), os.path.getsize(m)))
        else:
            pp = Path(p)
            if pp.is_file():
                files.append((str(pp.resolve()), pp.stat().st_size))
            elif pp.is_dir():
                for root, _, fnames in os.walk(pp):
                    for fname in fnames:
                        fp = os.path.join(root, fname)
                        files.append((os.path.abspath(fp), os.path.getsize(fp)))
        return files

    def _preview_table(self, items: list[tuple[str, int]], label: str) -> None:
        t = Table(
            show_header=True, header_style="bold",
            border_style=self.settings.table_border, min_width=60,
        )
        t.add_column("#", width=4, style="cyan")
        t.add_column("文件名", style="bold", min_width=20)
        t.add_column("大小", justify="right", style="green", width=10)
        for i, (name, sz) in enumerate(items[:50], 1):
            t.add_row(str(i), os.path.basename(name), _sizeof_fmt(sz))
        if len(items) > 50:
            t.add_row("...", "...", "...")
        console.print(t)
        total = sum(s for _, s in items)
        console.print(f"{S.dim}总计: {len(items)} 文件, {_sizeof_fmt(total)}{S.dim}\n")

    # ── 上传 ──────────────────────────────────────────────────────────
    async def cmd_upload(self, args: list[str]) -> None:
        parser = argparse.ArgumentParser(add_help=False)
        parser.add_argument("local")
        parser.add_argument("remote", nargs="?", default=".")
        parser.add_argument("-r", "--recurse", action="store_true")
        parser.add_argument("-j", "--jobs", type=int, default=None)
        parser.add_argument("-y", "--yes", action="store_true")
        try:
            parsed = parser.parse_args(args)
        except SystemExit:
            return
        jobs = parsed.jobs or self.settings.default_jobs

        matched = self._collect_local_files(parsed.local)
        if not matched:
            console.print(f"{S.error}没有匹配的文件:{S.dim} {parsed.local}")
            return

        console.print(f"\n{S.title}准备上传 {len(matched)} 个文件:{S.dim}")
        self._preview_table(matched, "上传预览")

        if not parsed.yes and not _ask_confirm("确认上传"):
            console.print(f"{S.dim}已取消{S.dim}")
            return

        remote = self.abs_path(parsed.remote)
        dir_id = await self.manager.create_dirs_by_path(remote.strip("/"))

        tasks: list[TransferTask] = []
        for fp, sz in matched:
            fname = os.path.basename(fp)
            tasks.append(TransferTask(
                remote_path=f"{remote.strip('/')}/{fname}",
                local_path=fp,
                size=sz,
            ))

        with Progress(
            SpinnerColumn(),
            TextColumn(f"{S.title}{{task.description}}{S.dim}"),
            BarColumn(bar_width=40),
            DownloadColumn(),
            TransferSpeedColumn(),
            TimeElapsedColumn(),
            TimeRemainingColumn(),
        ) as progress:
            main_tid = progress.add_task(
                f"上传 ({len(tasks)} 文件, 并发 {jobs})",
                total=len(tasks),
            )

            async def upload_one(idx2: int) -> None:
                t2 = tasks[idx2]
                tid = progress.add_task(f"{S.file}⬆ {os.path.basename(t2.local_path)}{S.dim}", total=t2.size)
                try:
                    start = time.time()
                    with open(t2.local_path, "rb") as f:
                        content = f.read()
                    await self.manager.upload_file(
                        dir_id, os.path.basename(t2.local_path), content, stream_len=t2.size
                    )
                    elapsed = time.time() - start
                    speed = t2.size / elapsed if elapsed > 0 else 0
                    progress.update(
                        tid, completed=t2.size,
                        description=f"{S.success}✓ {os.path.basename(t2.local_path)}{S.dim} "
                                    f"{S.dim}{_sizeof_fmt(speed)}/s{S.dim}",
                    )
                except Exception as e:
                    progress.update(
                        tid, description=f"{S.error}✗ {os.path.basename(t2.local_path)}: {e}{S.dim}",
                    )
                progress.update(main_tid, advance=1)

            sem = asyncio.Semaphore(jobs)

            async def bounded(idx2: int) -> None:
                async with sem:
                    await upload_one(idx2)

            await asyncio.gather(*[bounded(i) for i in range(len(tasks))])

        console.print()

    # ── 下载 ──────────────────────────────────────────────────────────
    async def cmd_download(self, args: list[str]) -> None:
        parser = argparse.ArgumentParser(add_help=False)
        parser.add_argument("remote")
        parser.add_argument("local", nargs="?", default=".")
        parser.add_argument("-r", "--recurse", action="store_true")
        parser.add_argument("-j", "--jobs", type=int, default=None)
        parser.add_argument("-y", "--yes", action="store_true")
        try:
            parsed = parser.parse_args(args)
        except SystemExit:
            return
        jobs = parsed.jobs or self.settings.default_jobs
        remote_raw = parsed.remote
        local = os.path.normpath(parsed.local)
        os.makedirs(local, exist_ok=True)

        is_glob = bool(re.search(r"[*?\[\]]", remote_raw))

        if is_glob:
            if "/" in remote_raw:
                parts2 = remote_raw.rsplit("/", 1)
                parent_part, pattern = parts2
                remote_dir = self.abs_path(parent_part) if parent_part else "/"
            else:
                pattern = remote_raw
                remote_dir = self.cwd

            info = await self.manager.get_resource_info_by_path(remote_dir.strip("/"))
            if not info or info.size != -1:
                console.print(f"{S.error}远程路径不存在或非目录:{S.dim} {remote_dir}")
                return

            console.print(f"{S.info}🔍 远程 glob 搜索:{S.dim} {S.title}{pattern}{S.dim} 在 {remote_dir}")
            tasks = await self._glob_remote(info.docid, remote_dir, pattern)

            if not tasks:
                console.print(f"{S.warning}没有匹配的文件{S.dim}")
                return

            console.print(f"\n{S.title}准备下载 {len(tasks)} 个文件:{S.dim}")
            self._preview_table([(t.remote_path, t.size) for t in tasks], "下载预览")

            if not parsed.yes and not _ask_confirm("确认下载"):
                console.print(f"{S.dim}已取消{S.dim}")
                return

            for tk in tasks:
                tk.local_path = os.path.join(local, os.path.basename(tk.remote_path))

            from .transfer import batch_download
            await batch_download(self.manager, tasks, jobs=jobs)
            console.print()
            return

        # ── 普通下载 ────────────────────────────────────────────────
        remote = self.abs_path(remote_raw)
        info = await self.manager.get_resource_info_by_path(remote.strip("/"))
        if not info:
            console.print(f"{S.error}远程路径不存在:{S.dim} {remote}")
            return

        if info.size != -1:
            dest = os.path.join(local, os.path.basename(remote))
            local_sz = os.path.getsize(dest) if os.path.exists(dest) else 0
            mode = "ab" if local_sz > 0 else "wb"
            headers = {}
            if local_sz < info.size:
                headers["Range"] = f"bytes={local_sz}-"
                console.print(f"{S.warning}断点续传，已下载 {local_sz} bytes{S.dim}")
            url, _ = await self.manager.get_download_url(info.docid)
            downloaded = local_sz
            start_t = time.time()
            from . import network
            with Progress(
                SpinnerColumn(),
                TextColumn(f"{S.title}{{task.description}}{S.dim}"),
                BarColumn(bar_width=40),
                DownloadColumn(),
                TransferSpeedColumn(),
                TimeElapsedColumn(),
                TimeRemainingColumn(),
            ) as progress:
                tid = progress.add_task(f"{S.file}⬇ {os.path.basename(remote)}{S.dim}", total=info.size)
                try:
                    with open(dest, mode) as f:
                        async for chunk in network.async_stream_download(
                            url, headers=headers, client=self.manager._client
                        ):
                            f.write(chunk)
                            downloaded += len(chunk)
                            elapsed = time.time() - start_t
                            spd = downloaded / elapsed if elapsed > 0 else 0
                            progress.update(
                                tid, completed=downloaded,
                                description=f"{S.file}⬇ {os.path.basename(remote)}{S.dim} "
                                            f"{S.success}{_sizeof_fmt(spd)}/s{S.dim}",
                            )
                    console.print(f"\n{S.success}✓{S.dim} 下载完成: {dest}")
                except Exception as e:
                    progress.update(tid, description=f"{S.error}✗ {os.path.basename(remote)}: {e}{S.dim}")
                    console.print(f"\n{S.error}下载失败:{S.dim} {e}")
            return

        if not parsed.recurse:
            console.print(f"{S.warning}{remote} 是目录，请使用 -r 递归下载{S.dim}")
            return

        tasks: list[TransferTask] = []
        base_local = os.path.join(local, os.path.basename(remote.rstrip("/")))
        os.makedirs(base_local, exist_ok=True)

        async def collect(pid: str, prem: str, ploc: str) -> None:
            dirs, files = await self.manager.list_dir(pid, by="name")
            for d in dirs:
                sd = os.path.join(ploc, d.name)
                os.makedirs(sd, exist_ok=True)
                await collect(d.docid, f"{prem}/{d.name}", sd)
            for f in files:
                fi = await self.manager.get_resource_info_by_path(f"{prem}/{f.name}".strip("/"))
                if fi:
                    tasks.append(TransferTask(
                        remote_path=f"{prem}/{f.name}",
                        local_path=os.path.join(ploc, f.name),
                        size=fi.size,
                        docid=fi.docid,
                    ))

        await collect(info.docid, remote, base_local)
        if not tasks:
            console.print(f"{S.dim}目录为空{S.dim}")
            return

        console.print(f"\n{S.title}准备下载 {len(tasks)} 个文件:{S.dim}")
        self._preview_table([(t.remote_path, t.size) for t in tasks], "下载预览")

        if not parsed.yes and not _ask_confirm("确认下载"):
            console.print(f"{S.dim}已取消{S.dim}")
            return

        from .transfer import batch_download
        await batch_download(self.manager, tasks, jobs=jobs)
        console.print()

    async def cmd_link(self, args: list[str]) -> None:
        parser = argparse.ArgumentParser(prog="link", add_help=False)
        parser.add_argument("path")
        parser.add_argument("-c", "--create", action="store_true")
        parser.add_argument("-d", "--delete", action="store_true")
        parser.add_argument("-e", "--expire", type=int, default=0)
        parser.add_argument("-p", "--password", action="store_true")
        try:
            parsed = parser.parse_args(args)
        except SystemExit:
            return
        target = self.abs_path(parsed.path)
        info = await self.manager.get_resource_info_by_path(target.strip("/"))
        if not info:
            console.print(f"{S.error}不存在:{S.dim} {target}")
            return
        if parsed.create:
            li = await self.manager.create_link(
                info.docid,
                end_time=parsed.expire if parsed.expire > 0 else None,
                enable_pass=parsed.password,
            )
            console.print(f"{S.success}✓{S.dim} 外链创建成功:")
            console.print(f"{S.info}链接:{S.dim} {li.link}")
            if li.password:
                console.print(f"{S.info}密码:{S.dim} {li.password}")
        elif parsed.delete:
            await self.manager.delete_link(info.docid)
            console.print(f"{S.success}✓{S.dim} 外链已删除")
        else:
            li = await self.manager.get_link(info.docid)
            if li:
                console.print(f"{S.info}链接:{S.dim} {li.link}")
                if li.password:
                    console.print(f"{S.info}密码:{S.dim} {li.password}")
                console.print(f"{S.info}权限:{S.dim} {li.perm}")
            else:
                console.print(f"{S.warning}该文件没有外链{S.dim}")


# ═══════════════════════════════════════════════════════════════════════════════
# 入口
# ═══════════════════════════════════════════════════════════════════════════════


def run_interactive_shell() -> None:
    try:
        asyncio.run(PanShell().run_async())
    except KeyboardInterrupt:
        pass
