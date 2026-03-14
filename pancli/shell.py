"""Stateful REPL Shell for AnyShare."""

from __future__ import annotations

import argparse
import getpass
import os
import shlex
import sys
import time

from prompt_toolkit import PromptSession
from prompt_toolkit.completion import Completer, Completion, ThreadedCompleter
from prompt_toolkit.document import Document
from prompt_toolkit.history import InMemoryHistory
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.tree import Tree
from rich.text import Text

from .api import ApiManager, InvalidRootException, WrongPasswordException
from .auth import rsa_encrypt
from .config import load_config, save_config, AppConfig
from .core import (
    _sizeof_fmt, _ts_fmt, _make_progress, _upload_impl, _download_impl, _move_or_copy
)

console = Console()

class AnyShareCompleter(Completer):
    def __init__(self, shell: PanShell):
        self.shell = shell
        self.cmds = [
            "ls", "cd", "pwd", "tree", "cat", "head", "tail", "touch",
            "stat", "mkdir", "rm", "mv", "cp", "upload", "download",
            "whoami", "logout", "su", "clear", "exit", "quit", "help"
        ]
        self._cache: dict[str, list[str]] = {}
        self._path_cache: dict[str, str] = {}

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

        # 如果是 upload 补全本地文件
        if cmd == "upload" and (len(args) == 2 if not text.endswith(" ") else len(args) == 1):
            import glob
            for match in glob.glob(word + "*"):
                yield Completion(match, start_position=-len(word), display=os.path.basename(match))
            return

        # 其他场景当作远程路径补全
        if "/" in word:
            parts = word.rsplit("/", 1)
            parent, prefix = parts[0] or "/", parts[1]
        else:
            parent, prefix = ".", word

        try:
            abs_parent = self.shell.abs_path(parent)
            docid = self._path_cache.get(abs_parent)
            if not docid:
                info = self.shell.manager.get_resource_info_by_path(abs_parent.strip("/"))
                if info and info.size == -1:
                    docid = info.docid
                    self._path_cache[abs_parent] = docid
            
            if docid:
                if docid not in self._cache:
                    dirs, files = self.shell.manager.list_dir(docid, by="name")
                    self._cache[docid] = [d["name"] + "/" for d in dirs] + [f["name"] for f in files]
                for name in self._cache[docid]:
                    if name.startswith(prefix):
                        yield Completion(name, start_position=-len(prefix))
        except Exception:
            pass

class PanShell:
    """The interactive stateful shell."""

    def __init__(self) -> None:
        self.cfg: AppConfig = load_config()
        self.manager: ApiManager | None = None
        self.cwd: str = "/"
        self.home_name: str = ""

    def login(self) -> None:
        if self.cfg.username:
            username = self.cfg.username
        else:
            username = console.input("[bold cyan]Username:[/bold cyan] ")
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
                self.manager = ApiManager(
                    self.cfg.host, username, password, self.cfg.pubkey,
                    encrypted=encrypted,
                    cached_token=self.cfg.cached_token.token or None,
                    cached_expire=self.cfg.cached_token.expires or None,
                )
                break
            except WrongPasswordException:
                console.print(f"[yellow]密码错误重试 ({retry + 1}/3)[/yellow]")
                time.sleep(1)
                password = getpass.getpass()
                encrypted = rsa_encrypt(password, self.cfg.pubkey)
                self.cfg.encrypted = encrypted

        if self.manager is None:
            self.cfg.username = None
            self.cfg.encrypted = None
            save_config(self.cfg)
            console.print("[bold red]登录失败[/bold red]")
            sys.exit(1)

        if self.manager._expires > 0:
            self.cfg.cached_token.token = self.manager._tokenid
            self.cfg.cached_token.expires = self.manager._expires
        save_config(self.cfg)

        console.print(f"[green]✓ 已连接: {self.cfg.host}[/green]")

        entrydoc = self.manager.get_entrydoc()
        if not entrydoc:
            console.print("[red]无法获取文档库根目录[/red]")
            sys.exit(1)
        self.home_name = entrydoc[0]["name"]
        self.cwd = f"/{self.home_name}"

    def abs_path(self, path: str) -> str:
        """Resolve path against CWD."""
        if path.startswith("/"):
            p = path
        else:
            p = f"{self.cwd}/{path}" if self.cwd != "/" else f"/{path}"
        
        parts = []
        for part in p.split("/"):
            if not part or part == ".": continue
            if part == "..":
                if parts: parts.pop()
            else:
                parts.append(part)
        return "/" + "/".join(parts)

    def run(self) -> None:
        self.login()
        session = PromptSession(
            history=InMemoryHistory(),
            completer=ThreadedCompleter(AnyShareCompleter(self))
        )

        while True:
            try:
                text = session.prompt(f"PanCLI [{self.cwd}] $ ")
                if not text.strip(): continue
                args = shlex.split(text)
                cmd = args[0]
                handler = getattr(self, f"cmd_{cmd}", self.cmd_unknown)
                handler(args[1:])
            except KeyboardInterrupt:
                continue
            except EOFError:
                break
            except Exception as e:
                console.print(f"[red]Error:[/red] {e}")

        if self.manager:
            self.manager.close()

    def cmd_unknown(self, args: list[str]) -> None:
        console.print("[yellow]Unknown command. Type 'help'.[/yellow]")

    def cmd_help(self, args: list[str]) -> None:
        console.print("\n[bold]PanCLI 命令参考手册[/bold]\n")
        
        t_base = Table("命令", "描述", box=None, show_header=False)
        for c, d in [("whoami", "查账户"), ("su [user]", "切账号"), ("logout", "清凭证"), ("clear", "清屏"), ("exit/quit", "退出")]:
            t_base.add_row(c, d)
        console.print(Panel(t_base, title="[cyan]环境与基础[/cyan]", border_style="cyan"))
        
        t_nav = Table("命令", "描述", box=None, show_header=False)
        for c, d in [("ls [dir] [-h]", "列表"), ("cd <dir>", "切换目录"), ("pwd", "显示当前路径"), ("tree [dir]", "树状图"), ("stat <path>", "查元数据")]:
            t_nav.add_row(c, d)
        console.print(Panel(t_nav, title="[green]导航与属性[/green]", border_style="green"))
        
        t_fs = Table("命令", "描述", box=None, show_header=False)
        for c, d in [("cat <file>", "打印全部内容"), ("head <file> [-n 行数]", "读头部行"), ("tail <file> [-n 行数]", "读尾部行"), ("touch <file>", "建空文件"), ("mkdir <dir>", "建目录"), ("rm <path> [-r]", "删除"), ("mv / cp", "移动或复制")]:
            t_fs.add_row(c, d)
        console.print(Panel(t_fs, title="[yellow]文件管理[/yellow]", border_style="yellow"))
        
        t_sync = Table("命令", "描述", box=None, show_header=False)
        for c, d in [("upload <本地> [远程] [-r]", "批量推入云端"), ("download <远程> [本地] [-r]", "批量拖入本地")]:
            t_sync.add_row(c, d)
        console.print(Panel(t_sync, title="[magenta]传输管理[/magenta]", border_style="magenta"))


    def cmd_exit(self, args: list[str]) -> None: raise EOFError
    def cmd_quit(self, args: list[str]) -> None: raise EOFError
    def cmd_clear(self, args: list[str]) -> None: console.clear()
    def cmd_pwd(self, args: list[str]) -> None: console.print(self.cwd)

    def cmd_whoami(self, args: list[str]) -> None:
        console.print(f"当前用户: [bold cyan]{self.cfg.username}[/bold cyan]")
        console.print(f"网盘 Host: [bold cyan]{self.cfg.host}[/bold cyan]")
        if self.cfg.encrypted:
            console.print("凭据状态: [green]已在本地保存密码[/green]")
        else:
            console.print("凭据状态: [yellow]未在本地保存密码[/yellow]")

    def cmd_logout(self, args: list[str]) -> None:
        self.cfg.username = None
        self.cfg.encrypted = None
        self.cfg.cached_token.token = ""
        save_config(self.cfg)
        console.print("[green]✓ 已清除本地凭据。将在下次命令或重启时生效。退出当前 Shell...[/green]")
        raise EOFError

    def cmd_su(self, args: list[str]) -> None:
        """切换账号：清除信息 -> 关闭当前实例 -> 重新登录"""
        self.cfg.username = args[0] if args else None
        self.cfg.encrypted = None
        self.cfg.cached_token.token = ""
        save_config(self.cfg)
        console.print(f"[cyan]准备切换账号，按要求重新登录...[/cyan]")
        if self.manager:
            self.manager.close()
        self.login()

    def cmd_cd(self, args: list[str]) -> None:
        target = self.abs_path(args[0]) if args else f"/{self.home_name}"
        if target == "/":
            self.cwd = "/"
            return
        info = self.manager.get_resource_info_by_path(target.strip("/"))
        if not info:
            console.print(f"[red]无此目录:[/red] {target}")
        elif info.size != -1:
            console.print(f"[red]非目录:[/red] {target}")
        else:
            self.cwd = target

    def cmd_ls(self, args: list[str]) -> None:
        parser = argparse.ArgumentParser(prog="ls", add_help=False)
        parser.add_argument("path", nargs="?", default=".")
        parser.add_argument("-h", "--human", action="store_true")
        try:
            parsed = parser.parse_args(args)
        except SystemExit:
            return
        
        target = self.abs_path(parsed.path)
        if target == "/":
            # Just print root entries implicitly
            entrydoc = self.manager.get_entrydoc()
            for root in entrydoc:
                console.print(f"📁 {root['name']}")
            return

        info = self.manager.get_resource_info_by_path(target.strip("/"))
        if not info:
            console.print(f"[red]不存在:[/red] {target}")
            return
        if info.size == -1:
            dirs, files = self.manager.list_dir(info.docid, by="name")
            table = Table(title=f"📂 {target}", show_header=True, border_style="dim")
            table.add_column("创建者", style="cyan"); table.add_column("大小", justify="right", style="green")
            table.add_column("修改时间", style="yellow"); table.add_column("名称", style="white bold")
            
            for d in dirs:
                table.add_row(d.get("creator", ""), Text("📁", style="blue"), _ts_fmt(d["modified"]), d["name"])
            for f in files:
                size_str = _sizeof_fmt(f["size"]) if parsed.human else str(f["size"])
                table.add_row(f.get("creator", ""), size_str, _ts_fmt(f["modified"]), f["name"])
            console.print(table)
        else:
            self.cmd_stat([target])

    def cmd_stat(self, args: list[str]) -> None:
        if not args: return
        target = self.abs_path(args[0])
        info = self.manager.get_resource_info_by_path(target.strip("/"))
        if not info:
            console.print(f"[red]不存在:[/red] {target}")
            return
        meta = self.manager.get_file_meta(info.docid)
        table = Table(title=f"📄 {target}", show_header=False, border_style="dim")
        table.add_column("Key", style="cyan bold"); table.add_column("Value")
        table.add_row("大小", _sizeof_fmt(meta.size))
        table.add_row("DocID", meta.docid)
        table.add_row("修改时间", _ts_fmt(meta.modified))
        table.add_row("修改者", meta.editor)
        table.add_row("标签", ", ".join(meta.tags) if meta.tags else "—")
        console.print(table)

    def cmd_tree(self, args: list[str]) -> None:
        target = self.abs_path(args[0] if args else ".")
        info = self.manager.get_resource_info_by_path(target.strip("/"))
        if not info or info.size != -1:
            console.print("[red]无效目录[/red]")
            return
        
        tree = Tree(f"📂 [bold blue]{target}[/bold blue]")
        def _build(docid, node):
            dirs, files = self.manager.list_dir(docid, by="name")
            for d in dirs:
                sub = node.add(f"📁 [blue]{d['name']}[/blue]")
                _build(d["docid"], sub)
            for f in files:
                node.add(f"📄 {f['name']} [dim]{_sizeof_fmt(f['size'])}[/dim]")
        
        _build(info.docid, tree)
        console.print(tree)

    def cmd_mkdir(self, args: list[str]) -> None:
        if not args: return
        target = self.abs_path(args[0]).strip("/")
        try:
            self.manager.create_dirs_by_path(target)
            console.print(f"[green]✓ 创建成功[/green]")
        except InvalidRootException:
            console.print("[red]无效根目录[/red]")

    def cmd_rm(self, args: list[str]) -> None:
        parser = argparse.ArgumentParser(prog="rm", add_help=False)
        parser.add_argument("path")
        parser.add_argument("-r", "--recurse", action="store_true")
        try:
            parsed = parser.parse_args(args)
        except SystemExit: return

        target = self.abs_path(parsed.path).strip("/")
        info = self.manager.get_resource_info_by_path(target)
        if not info:
            console.print("[red]不存在[/red]")
            return
        if info.size != -1:
            self.manager.delete_file(info.docid)
        else:
            if not parsed.recurse:
                console.print("[yellow]是目录，请加 -r[/yellow]")
                return
            self.manager.delete_dir(info.docid)
        console.print("[green]✓ 删除成功[/green]")

    def cmd_cat(self, args: list[str]) -> None:
        if not args: return
        self._print_file(args[0])

    def cmd_head(self, args: list[str]) -> None:
        parser = argparse.ArgumentParser(prog="head", add_help=False)
        parser.add_argument("path", nargs="?")
        parser.add_argument("-n", "--lines", type=int, default=10)
        try:
            parsed = parser.parse_args(args)
        except SystemExit: return
        if not parsed.path: return
        
        target = self.abs_path(parsed.path).strip("/")
        info = self.manager.get_resource_info_by_path(target)
        if not info or info.size == -1: return

        count = 0
        try:
            for chunk in self.manager.download_file_stream(info.docid):
                lines = chunk.split(b"\n")
                for i, line in enumerate(lines):
                    if i < len(lines) - 1:
                        sys.stdout.buffer.write(line + b"\n")
                        count += 1
                        if count >= parsed.lines: break
                    else:
                        sys.stdout.buffer.write(line)
                if count >= parsed.lines: break
            print()
        except BrokenPipeError:
            pass

    def cmd_tail(self, args: list[str]) -> None:
        parser = argparse.ArgumentParser(prog="tail", add_help=False)
        parser.add_argument("path", nargs="?")
        parser.add_argument("-n", "--lines", type=int, default=10)
        try:
            parsed = parser.parse_args(args)
        except SystemExit: return
        if not parsed.path: return
        
        target = self.abs_path(parsed.path).strip("/")
        info = self.manager.get_resource_info_by_path(target)
        if not info or info.size == -1: return

        import collections
        window = collections.deque(maxlen=parsed.lines)
        buffer = b""
        try:
            for chunk in self.manager.download_file_stream(info.docid):
                buffer += chunk
                while b"\n" in buffer:
                    line, buffer = buffer.split(b"\n", 1)
                    window.append(line)
            if buffer:
                window.append(buffer)

            for line in window:
                sys.stdout.buffer.write(line + (b"" if line.endswith(b"\r") else b"\n"))
        except BrokenPipeError:
            pass

    def _print_file(self, path: str, limit: int = -1) -> None:
        target = self.abs_path(path).strip("/")
        info = self.manager.get_resource_info_by_path(target)
        if not info or info.size == -1:
            console.print("[red]文件无效[/red]")
            return
        read = 0
        try:
            for chunk in self.manager.download_file_stream(info.docid):
                if limit > 0 and read + len(chunk) > limit:
                    sys.stdout.buffer.write(chunk[:limit - read])
                    break
                sys.stdout.buffer.write(chunk)
                read += len(chunk)
            sys.stdout.buffer.flush()
        except BrokenPipeError:
            pass
        print() # ensure newline

    def cmd_touch(self, args: list[str]) -> None:
        if not args: return
        target = self.abs_path(args[0]).strip("/")
        parent = "/".join(target.split("/")[:-1])
        name = target.split("/")[-1]
        pinfo = self.manager.get_resource_info_by_path(parent)
        if not pinfo:
            pdocid = self.manager.create_dirs_by_path(parent)
        else:
            pdocid = pinfo.docid
        self.manager.upload_file(pdocid, name, b"")
        console.print("[green]✓ 文件建立[/green]")

    def cmd_mv(self, args: list[str]) -> None: self._do_mv_cp(args, copy=False)
    def cmd_cp(self, args: list[str]) -> None: self._do_mv_cp(args, copy=True)

    def _do_mv_cp(self, args: list[str], copy: bool) -> None:
        parser = argparse.ArgumentParser(add_help=False)
        parser.add_argument("src")
        parser.add_argument("dst")
        parser.add_argument("-f", "--force", action="store_true")
        try:
            p = parser.parse_args(args)
        except SystemExit: return
        
        src = self.abs_path(p.src)
        dst = self.abs_path(p.dst)
        _move_or_copy(self.manager, src, dst, overwrite=p.force, copy=copy)

    def cmd_upload(self, args: list[str]) -> None:
        parser = argparse.ArgumentParser(add_help=False)
        parser.add_argument("local")
        parser.add_argument("remote", nargs="?", default=".")
        parser.add_argument("-r", "--recurse", action="store_true")
        try:
            p = parser.parse_args(args)
        except SystemExit: return
        _upload_impl(self.manager, p.local, self.abs_path(p.remote), allow_recurse=p.recurse)

    def cmd_download(self, args: list[str]) -> None:
        parser = argparse.ArgumentParser(add_help=False)
        parser.add_argument("remote")
        parser.add_argument("local", nargs="?", default=".")
        parser.add_argument("-r", "--recurse", action="store_true")
        try:
            p = parser.parse_args(args)
        except SystemExit: return
        _download_impl(self.manager, self.abs_path(p.remote), p.local, allow_recurse=p.recurse)

def run_shell() -> None:
    PanShell().run()
