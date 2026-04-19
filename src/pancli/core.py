"""核心业务逻辑层 — 被 main.py (Typer) 和 shell.py (REPL) 共同调用。

所有函数接收 AsyncApiManager，执行业务逻辑，使用 Rich 直接打印 UI。
这是 MVC 中的 Controller + Presenter 合一层。
"""

from __future__ import annotations

import asyncio
import collections
import fnmatch
import getpass
import os
import sys
import time
from pathlib import Path


from rich.console import Console

from rich.progress import (
    BarColumn,
    DownloadColumn,
    Progress,
    SpinnerColumn,
    TextColumn,
    TimeRemainingColumn,
    TransferSpeedColumn,
)
from rich.table import Table
from rich.text import Text
from rich.tree import Tree

from .api import AsyncApiManager, InvalidRootException, WrongPasswordException
from .auth import rsa_encrypt
from .config import load_config, save_config
from .models import AppConfig

__version__ = "0.1"

console = Console()


# ── 格式化工具 ──────────────────────────────────────────────────


def _sizeof_fmt(num: float, suffix: str = "") -> str:
    for unit in ("", "K", "M", "G", "T", "P", "E", "Z"):
        if abs(num) < 1024.0:
            return f"{num:3.1f}{unit}{suffix}"
        num /= 1024.0
    return f"{num:.1f}Y{suffix}"


def _ts_fmt(ts: int) -> str:
    if ts <= 0:
        return "—"
    # AnyShare 返回的时间戳可能是微秒、毫秒或秒级
    if ts > 1e15:       # 微秒
        ts_sec = ts / 1_000_000
    elif ts > 1e12:     # 毫秒
        ts_sec = ts / 1_000
    else:               # 秒
        ts_sec = ts
    return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(ts_sec))


# ── 路径解析 ────────────────────────────────────────────────────


def abs_path(cwd: str, path: str) -> str:
    """将相对路径解析为绝对路径。"""
    if path.startswith("/"):
        p = path
    else:
        p = f"{cwd}/{path}" if cwd != "/" else f"/{path}"
    parts: list[str] = []
    for part in p.split("/"):
        if not part or part == ".":
            continue
        if part == "..":
            if parts:
                parts.pop()
        else:
            parts.append(part)
    return "/" + "/".join(parts)


# ── 登录流程 ────────────────────────────────────────────────────


async def login(cfg: AppConfig) -> AsyncApiManager:
    """交互式登录，返回已鉴权的 AsyncApiManager。"""
    if cfg.username:
        username = cfg.username
    else:
        username = console.input("[bold cyan]Username:[/bold cyan] ")
        cfg.username = username

    encrypted: str | None = None
    if cfg.store_password and cfg.encrypted:
        encrypted = cfg.encrypted
    else:
        password = getpass.getpass()
        encrypted = rsa_encrypt(password, cfg.pubkey)
        if cfg.store_password:
            cfg.encrypted = encrypted

    for retry in range(3):
        try:
            manager = AsyncApiManager(
                cfg.host,
                username,
                None,
                cfg.pubkey,
                encrypted=encrypted,
                cached_token=cfg.cached_token.token or None,
                cached_expire=cfg.cached_token.expires or None,
            )
            await manager.ensure_token()
            break
        except WrongPasswordException:
            console.print(f"[yellow]密码错误，重试 ({retry + 1}/3)[/yellow]")
            password = getpass.getpass()
            encrypted = rsa_encrypt(password, cfg.pubkey)
            cfg.encrypted = encrypted
    else:
        cfg.username = None
        cfg.encrypted = None
        save_config(cfg)
        console.print("[bold red]登录失败[/bold red]")
        sys.exit(1)

    if manager._expires > 0:
        cfg.cached_token.token = manager._tokenid
        cfg.cached_token.expires = manager._expires
    save_config(cfg)

    console.print(f"[green]✓ 已连接: {cfg.host}[/green]")
    return manager


# ── 业务命令函数 ────────────────────────────────────────────────


async def do_ls(
    manager: AsyncApiManager,
    target: str,
    *,
    human: bool = True,
) -> None:
    """列出目录内容。"""
    if target == "/":
        entrydoc = await manager.get_entrydoc()
        for root in entrydoc:
            console.print(f"📁 {root['name']}")
        return

    info = await manager.get_resource_info_by_path(target.strip("/"))
    if not info:
        console.print(f"[red]不存在:[/red] {target}")
        return
    if info.size == -1:
        dirs, files = await manager.list_dir(info.docid, by="name")
        table = Table(title=f"📂 {target}", show_header=True, border_style="dim")
        table.add_column("创建者", style="cyan")
        table.add_column("大小", justify="right", style="green")
        table.add_column("修改时间", style="yellow")
        table.add_column("名称", style="white bold")
        for d in dirs:
            table.add_row(
                d.get("creator", ""), Text("📁", style="blue"),
                _ts_fmt(d["modified"]), d["name"],
            )
        for f in files:
            size_str = _sizeof_fmt(f["size"]) if human else str(f["size"])
            table.add_row(
                f.get("creator", ""), size_str,
                _ts_fmt(f["modified"]), f["name"],
            )
        console.print(table)
    else:
        await do_stat(manager, target)


async def do_stat(manager: AsyncApiManager, target: str) -> None:
    """显示文件/目录元信息。"""
    info = await manager.get_resource_info_by_path(target.strip("/"))
    if not info:
        console.print(f"[red]不存在:[/red] {target}")
        return
    meta = await manager.get_file_meta(info.docid)
    table = Table(title=f"📄 {target}", show_header=False, border_style="dim")
    table.add_column("Key", style="cyan bold")
    table.add_column("Value")
    table.add_row("大小", _sizeof_fmt(meta.size))
    table.add_row("DocID", meta.docid)
    table.add_row("修改时间", _ts_fmt(meta.modified))
    table.add_row("修改者", meta.editor)
    table.add_row("标签", ", ".join(meta.tags) if meta.tags else "—")
    console.print(table)


async def do_tree(manager: AsyncApiManager, target: str) -> None:
    """树状图显示目录结构。"""
    info = await manager.get_resource_info_by_path(target.strip("/"))
    if not info or info.size != -1:
        console.print("[red]无效目录[/red]")
        return

    tree = Tree(f"📂 [bold blue]{target}[/bold blue]")

    async def _build(docid: str, node: Tree) -> None:
        dirs, files = await manager.list_dir(docid, by="name")
        for d in dirs:
            sub = node.add(f"📁 [blue]{d['name']}[/blue]")
            await _build(d["docid"], sub)
        for f in files:
            node.add(f"📄 {f['name']} [dim]{_sizeof_fmt(f['size'])}[/dim]")

    await _build(info.docid, tree)
    console.print(tree)


async def do_cat(manager: AsyncApiManager, target: str) -> None:
    """打印文件全部内容。"""
    info = await manager.get_resource_info_by_path(target.strip("/"))
    if not info or info.size == -1:
        console.print("[red]文件无效[/red]")
        return
    try:
        async for chunk in manager.download_file_stream(info.docid):
            sys.stdout.buffer.write(chunk)
        sys.stdout.buffer.flush()
    except BrokenPipeError:
        pass
    print()


async def do_head(manager: AsyncApiManager, target: str, n: int = 10) -> None:
    """打印文件前 n 行。"""
    info = await manager.get_resource_info_by_path(target.strip("/"))
    if not info or info.size == -1:
        console.print("[red]文件无效[/red]")
        return
    count = 0
    try:
        async for chunk in manager.download_file_stream(info.docid):
            lines = chunk.split(b"\n")
            for i, line in enumerate(lines):
                if i < len(lines) - 1:
                    sys.stdout.buffer.write(line + b"\n")
                    count += 1
                    if count >= n:
                        break
                else:
                    sys.stdout.buffer.write(line)
            if count >= n:
                break
        print()
    except BrokenPipeError:
        pass


async def do_tail(manager: AsyncApiManager, target: str, n: int = 10) -> None:
    """打印文件末尾 n 行。"""
    info = await manager.get_resource_info_by_path(target.strip("/"))
    if not info or info.size == -1:
        console.print("[red]文件无效[/red]")
        return
    window: collections.deque[bytes] = collections.deque(maxlen=n)
    buf = b""
    try:
        async for chunk in manager.download_file_stream(info.docid):
            buf += chunk
            while b"\n" in buf:
                line, buf = buf.split(b"\n", 1)
                window.append(line)
        if buf:
            window.append(buf)
        for line in window:
            sys.stdout.buffer.write(line + b"\n")
    except BrokenPipeError:
        pass


async def do_mkdir(manager: AsyncApiManager, target: str) -> None:
    """创建目录。"""
    try:
        await manager.create_dirs_by_path(target.strip("/"))
        console.print("[green]✓ 创建成功[/green]")
    except InvalidRootException:
        console.print("[red]无效根目录[/red]")


async def do_touch(manager: AsyncApiManager, target: str) -> None:
    """创建空文件。"""
    target = target.strip("/")
    parent = "/".join(target.split("/")[:-1])
    name = target.split("/")[-1]
    pinfo = await manager.get_resource_info_by_path(parent)
    if not pinfo:
        pdocid = await manager.create_dirs_by_path(parent)
    else:
        pdocid = pinfo.docid
    await manager.upload_file(pdocid, name, b"")
    console.print("[green]✓ 文件建立[/green]")


async def do_rm(manager: AsyncApiManager, target: str, recurse: bool = False) -> None:
    """删除文件或目录。"""
    info = await manager.get_resource_info_by_path(target.strip("/"))
    if not info:
        console.print("[red]不存在[/red]")
        return
    if info.size != -1:
        await manager.delete_file(info.docid)
    else:
        if not recurse:
            console.print("[yellow]是目录，请加 -r 参数[/yellow]")
            return
        await manager.delete_dir(info.docid)
    console.print("[green]✓ 删除成功[/green]")


async def do_mv(
    manager: AsyncApiManager, src: str, dst: str, overwrite: bool = False
) -> None:
    """移动文件/目录。"""
    await _move_or_copy(manager, src, dst, overwrite=overwrite, copy=False)


async def do_cp(
    manager: AsyncApiManager, src: str, dst: str, overwrite: bool = False
) -> None:
    """复制文件/目录。"""
    await _move_or_copy(manager, src, dst, overwrite=overwrite, copy=True)


async def _move_or_copy(
    manager: AsyncApiManager,
    src: str,
    dst: str,
    *,
    overwrite: bool = False,
    copy: bool = False,
) -> None:
    src_info = await manager.get_resource_info_by_path(src.strip("/"))
    if not src_info:
        console.print(f"[red]源不存在:[/red] {src}")
        return
    dst_info = await manager.get_resource_info_by_path(dst.strip("/"))
    if not dst_info:
        console.print(f"[red]目标不存在:[/red] {dst}")
        return
    fn = manager.copy_file if copy else manager.move_file
    await fn(src_info.docid, dst_info.docid, overwrite_on_dup=overwrite)
    action = "复制" if copy else "移动"
    console.print(f"[green]✓ {action}成功[/green]")


# ── 并发传输 ────────────────────────────────────────────────────


async def do_upload(
    manager: AsyncApiManager,
    local_path: str,
    remote_target: str,
    *,
    recurse: bool = False,
    jobs: int = 4,
) -> None:
    """上传文件或目录。"""
    local = Path(local_path)
    if not local.exists():
        console.print(f"[red]本地路径不存在:[/red] {local_path}")
        return

    remote_info = await manager.get_resource_info_by_path(remote_target.strip("/"))
    if remote_info and remote_info.size != -1:
        # 远端目标是文件而不是目录
        console.print("[red]远程目标必须是目录[/red]")
        return

    if not remote_info:
        parent_docid = await manager.create_dirs_by_path(remote_target.strip("/"))
    else:
        parent_docid = remote_info.docid

    if local.is_file():
        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            BarColumn(),
            DownloadColumn(),
            TransferSpeedColumn(),
            TimeRemainingColumn(),
        ) as progress:
            size = local.stat().st_size
            task = progress.add_task(f"⬆ {local.name}", total=size)
            content = local.read_bytes()
            await manager.upload_file(parent_docid, local.name, content)
            progress.update(task, completed=size)
        console.print("[green]✓ 上传成功[/green]")
    elif local.is_dir() and recurse:
        files_to_upload: list[tuple[Path, str]] = []
        for root, _, filenames in os.walk(local):
            rel = os.path.relpath(root, local)
            for fn in filenames:
                fp = Path(root) / fn
                rp = f"{remote_target.strip('/')}/{rel}/{fn}" if rel != "." else f"{remote_target.strip('/')}/{fn}"
                files_to_upload.append((fp, rp))

        sem = asyncio.Semaphore(jobs)

        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            BarColumn(),
            DownloadColumn(),
            TransferSpeedColumn(),
        ) as progress:
            overall = progress.add_task("[bold]总进度", total=len(files_to_upload))

            async def _upload_one(fp: Path, rp: str) -> None:
                async with sem:
                    rp_stripped = rp.strip("/")
                    parent = "/".join(rp_stripped.split("/")[:-1])
                    name = rp_stripped.split("/")[-1]
                    pinfo = await manager.get_resource_info_by_path(parent)
                    if not pinfo:
                        pdocid = await manager.create_dirs_by_path(parent)
                    else:
                        pdocid = pinfo.docid
                    content = fp.read_bytes()
                    await manager.upload_file(pdocid, name, content)
                    progress.advance(overall)

            tasks = [_upload_one(fp, rp) for fp, rp in files_to_upload]
            await asyncio.gather(*tasks)
        console.print(f"[green]✓ 上传完成 ({len(files_to_upload)} 个文件)[/green]")
    else:
        console.print("[yellow]是目录，请加 -r 参数[/yellow]")


async def do_download(
    manager: AsyncApiManager,
    remote_target: str,
    local_path: str = ".",
    *,
    recurse: bool = False,
    jobs: int = 4,
) -> None:
    """下载文件或目录，支持并发和断点续传。"""
    info = await manager.get_resource_info_by_path(remote_target.strip("/"))
    if not info:
        console.print(f"[red]远程路径不存在:[/red] {remote_target}")
        return

    if info.size != -1:
        # 单文件下载（含断点续传）
        local_file = Path(local_path)
        if local_file.is_dir():
            local_file = local_file / info.name

        resume_from = 0
        if local_file.exists():
            local_size = local_file.stat().st_size
            if local_size == info.size:
                console.print(f"[dim]跳过（已完成）: {info.name}[/dim]")
                return
            if local_size < info.size:
                resume_from = local_size

        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            BarColumn(),
            DownloadColumn(),
            TransferSpeedColumn(),
            TimeRemainingColumn(),
        ) as progress:
            task = progress.add_task(f"⬇ {info.name}", total=info.size, completed=resume_from)
            mode = "ab" if resume_from > 0 else "wb"
            with open(local_file, mode) as f:
                async for chunk in manager.download_file_stream(
                    info.docid, resume_from=resume_from
                ):
                    f.write(chunk)
                    progress.advance(task, len(chunk))
        console.print("[green]✓ 下载成功[/green]")

    elif recurse:
        # 递归下载目录
        items: list[tuple[dict, str]] = []  # (file_info_dict, local_dest)

        async def _collect(docid: str, local_dir: str) -> None:
            os.makedirs(local_dir, exist_ok=True)
            dirs, files = await manager.list_dir(docid, by="name")
            for d in dirs:
                await _collect(d["docid"], os.path.join(local_dir, d["name"]))
            for f in files:
                items.append((f, os.path.join(local_dir, f["name"])))

        await _collect(info.docid, os.path.join(local_path, info.name))

        sem = asyncio.Semaphore(jobs)
        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            BarColumn(),
            DownloadColumn(),
            TransferSpeedColumn(),
        ) as progress:
            overall = progress.add_task("[bold]总进度", total=len(items))

            async def _dl_one(finfo: dict, dest: str) -> None:
                async with sem:
                    remote_size = finfo.get("size", 0)
                    resume = 0
                    if os.path.exists(dest):
                        local_sz = os.path.getsize(dest)
                        if local_sz == remote_size:
                            progress.advance(overall)
                            return
                        if local_sz < remote_size:
                            resume = local_sz

                    sub = progress.add_task(f"  ⬇ {finfo['name']}", total=remote_size, completed=resume)
                    mode = "ab" if resume > 0 else "wb"
                    with open(dest, mode) as f:
                        async for chunk in manager.download_file_stream(
                            finfo["docid"], resume_from=resume
                        ):
                            f.write(chunk)
                            progress.advance(sub, len(chunk))
                    progress.remove_task(sub)
                    progress.advance(overall)

            tasks = [_dl_one(fi, dest) for fi, dest in items]
            await asyncio.gather(*tasks)
        console.print(f"[green]✓ 下载完成 ({len(items)} 个文件)[/green]")
    else:
        console.print("[yellow]是目录，请加 -r 参数[/yellow]")


# ── 全局搜索 ────────────────────────────────────────────────────


async def do_find(
    manager: AsyncApiManager,
    keyword: str,
    root_path: str,
    *,
    max_depth: int = 5,
    jobs: int = 8,
) -> None:
    """递归搜索文件名匹配 keyword 的文件（支持 * ? 通配符）。

    如果 keyword 不含通配符，自动包裹为 *keyword*。
    """
    if "*" not in keyword and "?" not in keyword:
        keyword = f"*{keyword}*"
    root_info = await manager.get_resource_info_by_path(root_path.strip("/"))
    if not root_info or root_info.size != -1:
        console.print("[red]搜索根目录无效[/red]")
        return

    results: list[tuple[str, int, int]] = []  # (path, size, modified)
    sem = asyncio.Semaphore(jobs)

    async def _search(docid: str, current_path: str, depth: int) -> None:
        if depth > max_depth:
            return
        async with sem:
            dirs, files = await manager.list_dir(docid, by="name")
        for f in files:
            if fnmatch.fnmatch(f["name"].lower(), keyword.lower()):
                results.append((f"{current_path}/{f['name']}", f["size"], f["modified"]))
        tasks = []
        for d in dirs:
            if fnmatch.fnmatch(d["name"].lower(), keyword.lower()):
                results.append((f"{current_path}/{d['name']}/", -1, d["modified"]))
            tasks.append(_search(d["docid"], f"{current_path}/{d['name']}", depth + 1))
        if tasks:
            await asyncio.gather(*tasks)

    with console.status("[cyan]搜索中...[/cyan]"):
        await _search(root_info.docid, root_path.rstrip("/"), 0)

    if not results:
        console.print(f'[yellow]未找到包含 "{keyword}" 的文件[/yellow]')
        return

    table = Table(title=f'🔍 搜索结果: "{keyword}"', border_style="dim")
    table.add_column("路径", style="cyan")
    table.add_column("大小", justify="right", style="green")
    table.add_column("修改时间", style="yellow")
    for path, size, modified in sorted(results):
        size_str = "📁" if size == -1 else _sizeof_fmt(size)
        table.add_row(path, size_str, _ts_fmt(modified))
    console.print(table)
    console.print(f"[dim]共 {len(results)} 条结果[/dim]")
