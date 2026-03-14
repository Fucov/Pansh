"""Core helper functions for AnyShare CLI."""

from __future__ import annotations

import os
import time
import sys

from rich.console import Console
from rich.progress import (
    BarColumn, DownloadColumn, Progress, TextColumn, TimeRemainingColumn, TransferSpeedColumn
)

from .api import ApiManager

console = Console()
__version__ = "3.0.0"

def _sizeof_fmt(num: float, suffix: str = "") -> str:
    for unit in ("", "K", "M", "G", "T", "P", "E", "Z"):
        if abs(num) < 1024.0:
            return f"{num:3.1f}{unit}{suffix}"
        num /= 1024.0
    return f"{num:.1f}Yi{suffix}"


def _ts_fmt(us: int) -> str:
    """微秒时间戳 → 可读日期。"""
    return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(us / 1_000_000))


def _make_progress() -> Progress:
    """构建 Rich 进度条（上传/下载统一样式）。"""
    return Progress(
        TextColumn("[bold blue]{task.description}"),
        BarColumn(bar_width=40),
        DownloadColumn(),
        TransferSpeedColumn(),
        TimeRemainingColumn(),
        console=console,
    )


def _upload_impl(
    m: ApiManager,
    local_path: str,
    remote_dir: str,
    *,
    rename: str | None = None,
    allow_recurse: bool = False,
) -> None:
    remote_dir = remote_dir.strip("/")
    local_path = os.path.normpath(local_path)
    remote_name = rename or os.path.basename(os.path.abspath(local_path))

    if not os.path.exists(local_path):
        console.print(f"[red]本地路径不存在:[/red] {local_path}")
        return

    if os.path.isfile(local_path):
        file_size = os.path.getsize(local_path)
        dir_id = m.create_dirs_by_path(remote_dir)

        if file_size > 1024 * 1024:
            with _make_progress() as progress:
                task = progress.add_task(f"⬆  {remote_name}", total=file_size)
                with open(local_path, "rb") as f:
                    class _ProgressReader:
                        def __init__(self, fp, pg, tid):
                            self._fp = fp
                            self._pg = pg
                            self._tid = tid
                        def read(self, size: int = -1) -> bytes:
                            data = self._fp.read(size)
                            if data:
                                self._pg.update(self._tid, advance=len(data))
                            return data
                    wrapped = _ProgressReader(f, progress, task)
                    m.upload_file(dir_id, remote_name, wrapped, stream_len=file_size)
        else:
            with open(local_path, "rb") as f:
                content = f.read()
            console.print(f"[dim]上传中...[/dim] {remote_name} ({_sizeof_fmt(file_size)})")
            m.upload_file(dir_id, remote_name, content)
        console.print(f"[green]✓[/green] 上传完成: {remote_name}")
    else:
        if allow_recurse:
            entries = os.listdir(local_path)
            full_remote = remote_dir + "/" + remote_name
            for entry in entries:
                full_local = os.path.join(local_path, entry)
                _upload_impl(m, full_local, full_remote, allow_recurse=True)
            if not entries:
                m.create_dirs_by_path(full_remote)
        else:
            console.print(f"[yellow]{local_path} 是目录，请使用 -r 递归上传[/yellow]")


def _download_impl(
    m: ApiManager,
    remote_path: str,
    local_dir: str,
    *,
    rename: str | None = None,
    allow_recurse: bool = False,
) -> None:
    remote_path = remote_path.strip("/")
    local_name = rename or os.path.basename(remote_path)
    file_info = m.get_resource_info_by_path(remote_path)

    if file_info is None:
        console.print(f"[red]远程路径不存在:[/red] {remote_path}")
        return

    if file_info.size != -1:
        os.makedirs(local_dir, exist_ok=True)
        dest = os.path.join(local_dir, local_name)
        with _make_progress() as progress:
            task = progress.add_task(f"⬇  {local_name}", total=file_info.size)
            with open(dest, "wb") as f:
                for chunk in m.download_file_stream(file_info.docid):
                    f.write(chunk)
                    progress.update(task, advance=len(chunk))
        console.print(f"[green]✓[/green] 下载完成: {dest}")
    else:
        if allow_recurse:
            dirs, files = m.list_dir(file_info.docid, by="name")
            full_local = os.path.join(local_dir, local_name)
            for d in dirs:
                _download_impl(m, remote_path + "/" + d["name"], full_local, allow_recurse=True)
            for f in files:
                _download_impl(m, remote_path + "/" + f["name"], full_local, allow_recurse=True)
        else:
            console.print(f"[yellow]{remote_path} 是目录，请使用 -r 递归下载[/yellow]")


def _move_or_copy(
    m: ApiManager, src: str, dst: str, *, overwrite: bool = False, copy: bool = False
) -> None:
    action = "复制" if copy else "移动"
    src_parts = src.strip("/").split("/")
    dst_parts = dst.strip("/").split("/")
    dst_name = dst_parts[-1]
    dst_parent = "/".join(dst_parts[:-1])

    if src_parts == dst_parts:
        console.print("[red]源路径与目标路径相同[/red]")
        return

    src_info = m.get_resource_info_by_path(src.strip("/"))
    if src_info is None:
        console.print(f"[red]源路径不存在:[/red] {src}")
        return

    dst_info = m.get_resource_info_by_path(dst.strip("/"))

    if dst_info is not None and dst_info.size == -1:
        if src_parts[:-1] == dst_parts:
            console.print("[dim]无需操作[/dim]")
            return
        if src_parts == dst_parts[: len(src_parts)]:
            console.print("[red]不能移动到子目录[/red]")
            return
        if copy:
            m.copy_file(src_info.docid, dst_info.docid, overwrite_on_dup=overwrite)
        else:
            m.move_file(src_info.docid, dst_info.docid, overwrite_on_dup=overwrite)
        console.print(f"[green]✓[/green] {action}完成")
        return

    if dst_info is None:
        if src_parts[:-1] == dst_parts[:-1]:
            if copy:
                dst_parent_info = m.get_resource_info_by_path(dst_parent)
                new_id, _ = m.copy_file(src_info.docid, dst_parent_info.docid, rename_on_dup=True)
                m.rename_file(new_id, dst_name)
            else:
                m.rename_file(src_info.docid, dst_name)
            console.print(f"[green]✓[/green] 重命名: {src} → {dst}")
            return
        if src_parts == dst_parts[: len(src_parts)]:
            console.print("[red]不能移动到子目录[/red]")
            return
        dst_parent_info = m.get_resource_info_by_path(dst_parent)
        if dst_parent_info is None:
            console.print("[red]目标父目录不存在[/red]")
            return
        if copy:
            new_id, new_name = m.copy_file(src_info.docid, dst_parent_info.docid, rename_on_dup=True)
        else:
            new_id, new_name = m.move_file(src_info.docid, dst_parent_info.docid, rename_on_dup=True)
        if new_name != dst_name:
            m.rename_file(new_id, dst_name)
        console.print(f"[green]✓[/green] {action}完成: {src} → {dst}")
        return

    if src_info.size == -1:
        console.print("[red]不能将目录移动到文件位置[/red]")
        return

    if overwrite:
        dst_parent_info = m.get_resource_info_by_path(dst_parent)
        assert dst_parent_info is not None
        m.delete_file(dst_info.docid)
        if src_parts[:-1] == dst_parts[:-1]:
            if copy:
                new_id, _ = m.copy_file(src_info.docid, dst_parent_info.docid, rename_on_dup=True)
                m.rename_file(new_id, dst_name)
            else:
                m.rename_file(src_info.docid, dst_name)
        else:
            if copy:
                new_id, new_name = m.copy_file(src_info.docid, dst_parent_info.docid, rename_on_dup=True)
            else:
                new_id, new_name = m.move_file(src_info.docid, dst_parent_info.docid, rename_on_dup=True)
            if new_name != dst_name:
                m.rename_file(new_id, dst_name)
        console.print(f"[green]✓[/green] {action}并覆盖完成")
    else:
        console.print(f"[yellow]{dst} 已存在，使用 -f 覆盖[/yellow]")
