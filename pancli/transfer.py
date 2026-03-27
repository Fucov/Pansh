"""Concurrent transfer engine with Rich progress bars and resume support."""

from __future__ import annotations

import asyncio
import os
import time
from pathlib import Path
from typing import TYPE_CHECKING

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

from .models import TransferStatus, TransferTask

if TYPE_CHECKING:
    from .api import AsyncApiManager


def _sizeof_fmt(num: float, suffix: str = "") -> str:
    for unit in ("", "K", "M", "G", "T", "P", "E", "Z"):
        if abs(num) < 1024.0:
            return f"{num:3.1f}{unit}{suffix}"
        num /= 1024.0
    return f"{num:.1f}Yi{suffix}"


# ── 单文件下载任务 ──────────────────────────────────────────────


async def _download_single_file(
    manager: "AsyncApiManager",
    task: TransferTask,
    progress: Progress,
    task_id: TaskID,
    semaphore: asyncio.Semaphore,
) -> None:
    """单文件异步下载任务（支持断点续传）。"""
    async with semaphore:
        try:
            url, total_size = await manager.get_download_url(task.docid)

            local_path = Path(task.local_path)
            local_size = 0
            headers = {}

            if local_path.exists():
                local_size = local_path.stat().st_size
                if local_size < total_size:
                    headers["Range"] = f"bytes={local_size}-"

            start_time = time.time()
            downloaded = local_size
            mode = "ab" if local_size > 0 else "wb"

            progress.update(task_id, description=f"[cyan]⬇ {local_path.name} [cyan]0.0/s[dim] ...")

            with open(local_path, mode) as f:
                async for chunk in manager._client.stream("GET", url, headers=headers):
                    f.write(chunk)
                    downloaded += len(chunk)
                    elapsed = time.time() - start_time
                    speed = downloaded / elapsed if elapsed > 0 else 0
                    progress.update(
                        task_id,
                        completed=downloaded,
                        description=f"[cyan]⬇ {local_path.name} "
                                    f"[green]{_sizeof_fmt(speed)}/s[/green]",
                    )

            task.transferred = downloaded
            task.status = TransferStatus.COMPLETED
            progress.update(
                task_id,
                completed=total_size,
                description=f"[green]✓ {local_path.name}",
            )

        except Exception as e:
            task.status = TransferStatus.FAILED
            task.error = str(e)
            progress.update(
                task_id,
                description=f"[red]✗ {Path(task.remote_path).name}: {e}",
            )


# ── 单文件上传任务 ──────────────────────────────────────────────


async def _upload_single_file(
    manager: "AsyncApiManager",
    task: TransferTask,
    remote_parent_id: str,
    progress: Progress,
    task_id: TaskID,
    semaphore: asyncio.Semaphore,
) -> None:
    """单文件异步上传任务。"""
    async with semaphore:
        local_path = Path(task.local_path)
        progress.update(task_id, description=f"[magenta]⬆ {local_path.name} [magenta]0.0/s[dim] ...")
        try:
            file_size = local_path.stat().st_size
            start_time = time.time()

            class ProgressReader:
                __slots__ = ("_fp", "_pg", "_tid", "_task", "_start", "_uploaded")

                def __init__(self, fp, pg, tid, task_ref):
                    self._fp = fp
                    self._pg = pg
                    self._tid = tid
                    self._task = task_ref
                    self._start = start_time
                    self._uploaded = 0

                def read(self, size: int = -1) -> bytes:
                    nonlocal manager
                    data = self._fp.read(size)
                    if data:
                        self._uploaded += len(data)
                        elapsed = time.time() - self._start
                        speed = self._uploaded / elapsed if elapsed > 0 else 0
                        self._pg.update(
                            self._tid,
                            advance=len(data),
                            description=f"[magenta]⬆ {self._task.local_path} "
                                        f"[green]{_sizeof_fmt(speed)}/s[/green]",
                        )
                    return data

            with open(local_path, "rb") as f:
                reader = ProgressReader(f, progress, task_id, task)
                await manager.upload_file(
                    remote_parent_id,
                    local_path.name,
                    reader,
                    stream_len=file_size,
                )

            task.transferred = file_size
            task.status = TransferStatus.COMPLETED
            progress.update(
                task_id,
                completed=file_size,
                description=f"[green]✓ {local_path.name}",
            )

        except Exception as e:
            task.status = TransferStatus.FAILED
            task.error = str(e)
            progress.update(
                task_id,
                description=f"[red]✗ {local_path.name}: {e}",
            )


# ── 并发批量下载 ────────────────────────────────────────────────


async def batch_download(
    manager: "AsyncApiManager",
    tasks: list[TransferTask],
    jobs: int = 4,
) -> list[TransferTask]:
    """并发下载多个文件，带 Rich 多行进度条。"""
    if not tasks:
        return []

    semaphore = asyncio.Semaphore(jobs)

    with Progress(
        SpinnerColumn(),
        TextColumn("[bold blue]{task.description}"),
        BarColumn(bar_width=40),
        DownloadColumn(),
        TransferSpeedColumn(),
        TimeElapsedColumn(),
        TimeRemainingColumn(),
    ) as progress:
        main_task = progress.add_task(
            f"[bold]批量下载 ({len(tasks)} 文件, 并发 {jobs})",
            total=len(tasks),
        )

        file_progress_ids: list[TaskID] = []
        for t in tasks:
            tid = progress.add_task(f"[cyan]⬇ {Path(t.remote_path).name}", total=t.size)
            file_progress_ids.append(tid)

        async def download_wrapper(idx: int) -> None:
            t = tasks[idx]
            tid = file_progress_ids[idx]
            await _download_single_file(manager, t, progress, tid, semaphore)
            progress.update(main_task, advance=1)

        await asyncio.gather(*[download_wrapper(i) for i in range(len(tasks))])

    # 打印结果摘要
    from rich.console import Console as _C
    _c = _C(markup=True)
    ok = [t for t in tasks if t.status == TransferStatus.COMPLETED]
    fail = [t for t in tasks if t.status == TransferStatus.FAILED]
    if fail:
        _c.print(f"\n[red]✗ 失败 {len(fail)} 个:[/red]")
        for t in fail:
            _c.print(f"  [red]•[/red] {Path(t.remote_path).name} — {t.error}")
    if ok:
        _c.print(f"\n[green]✓ 成功 {len(ok)} 个[/green]")
    return tasks


# ── 并发批量上传 ────────────────────────────────────────────────


async def batch_upload(
    manager: "AsyncApiManager",
    tasks: list[TransferTask],
    remote_parent_id: str,
    jobs: int = 4,
) -> list[TransferTask]:
    """并发上传多个文件，带 Rich 多行进度条。"""
    if not tasks:
        return []

    semaphore = asyncio.Semaphore(jobs)

    with Progress(
        SpinnerColumn(),
        TextColumn("[bold magenta]{task.description}"),
        BarColumn(bar_width=40),
        DownloadColumn(),
        TransferSpeedColumn(),
        TimeElapsedColumn(),
        TimeRemainingColumn(),
    ) as progress:
        main_task = progress.add_task(
            f"[bold]批量上传 ({len(tasks)} 文件, 并发 {jobs})",
            total=len(tasks),
        )

        file_progress_ids: list[TaskID] = []
        for t in tasks:
            size = Path(t.local_path).stat().st_size if Path(t.local_path).exists() else 0
            tid = progress.add_task(f"[magenta]⬆ {Path(t.local_path).name}", total=size)
            file_progress_ids.append(tid)

        async def upload_wrapper(idx: int) -> None:
            t = tasks[idx]
            tid = file_progress_ids[idx]
            await _upload_single_file(
                manager, t, remote_parent_id, progress, tid, semaphore
            )
            progress.update(main_task, advance=1)

        await asyncio.gather(*[upload_wrapper(i) for i in range(len(tasks))])

    from rich.console import Console as _C
    _c = _C(markup=True)
    ok = [t for t in tasks if t.status == TransferStatus.COMPLETED]
    fail = [t for t in tasks if t.status == TransferStatus.FAILED]
    if fail:
        _c.print(f"\n[red]✗ 失败 {len(fail)} 个:[/red]")
        for t in fail:
            _c.print(f"  [red]•[/red] {t.remote_path} — {t.error}")
    if ok:
        _c.print(f"\n[green]✓ 成功 {len(ok)} 个[/green]")
    return tasks


# ── 便捷批量构建函数 ────────────────────────────────────────────


def build_download_tasks(
    manager: "AsyncApiManager",
    remote_path: str,
    local_dir: str,
    *,
    allow_recurse: bool = False,
) -> list[TransferTask]:
    tasks: list[TransferTask] = []

    async def _collect():
        info = await manager.get_resource_info_by_path(remote_path.strip("/"))
        if info is None:
            return

        os.makedirs(local_dir, exist_ok=True)

        if info.size != -1:
            local_name = os.path.basename(remote_path)
            tasks.append(
                TransferTask(
                    remote_path=remote_path,
                    local_path=os.path.join(local_dir, local_name),
                    size=info.size,
                    docid=info.docid,
                )
            )
        elif allow_recurse:
            dirs, files = await manager.list_dir(info.docid, by="name")
            base_local = os.path.join(local_dir, os.path.basename(remote_path.rstrip("/")))
            os.makedirs(base_local, exist_ok=True)
            for d in dirs:
                sub_tasks = build_download_tasks(
                    manager,
                    remote_path + "/" + d.name,
                    base_local,
                    allow_recurse=True,
                )
                tasks.extend(sub_tasks)
            for f in files:
                sub_tasks = build_download_tasks(
                    manager,
                    remote_path + "/" + f.name,
                    base_local,
                    allow_recurse=False,
                )
                tasks.extend(sub_tasks)

    return tasks


def build_upload_tasks(
    local_path: str,
    remote_dir: str,
    *,
    allow_recurse: bool = False,
) -> list[TransferTask]:
    tasks: list[TransferTask] = []
    local_path = os.path.normpath(local_path)
    remote_name = os.path.basename(os.path.abspath(local_path))
    remote_base = remote_dir.strip("/") + "/" + remote_name

    if os.path.isfile(local_path):
        tasks.append(
            TransferTask(
                remote_path=remote_base,
                local_path=local_path,
                size=os.path.getsize(local_path),
            )
        )
    elif allow_recurse:
        for entry in os.listdir(local_path):
            full_local = os.path.join(local_path, entry)
            full_remote = remote_base + "/" + entry
            if os.path.isfile(full_local):
                tasks.append(
                    TransferTask(
                        remote_path=full_remote,
                        local_path=full_local,
                        size=os.path.getsize(full_local),
                    )
                )
            elif os.path.isdir(full_local):
                tasks.extend(
                    build_upload_tasks(full_local, remote_dir + "/" + remote_name, allow_recurse=True)
                )
    return tasks
