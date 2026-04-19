"""Upload and download orchestration."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import TYPE_CHECKING

from .models import TransferStatus, TransferTask
from .progress import Speedometer, create_transfer_progress, update_progress_fields
from .settings import load_settings

if TYPE_CHECKING:
    from rich.console import Console

    from .api import AsyncApiManager


async def batch_download(
    manager: "AsyncApiManager",
    tasks: list[TransferTask],
    *,
    jobs: int,
    console: "Console",
) -> list[TransferTask]:
    if not tasks:
        return []
    settings = load_settings()
    semaphore = asyncio.Semaphore(jobs)
    total_bytes = sum(max(task.size, 0) for task in tasks)
    with create_transfer_progress(console, settings.refresh_per_second) as progress:
        overall_meter = Speedometer(alpha=settings.ema_alpha)
        overall_task = progress.add_task(
            "overall",
            total=max(total_bytes, 1),
            filename="overall",
            status=TransferStatus.QUEUED.value,
            current_rate="-",
            average_rate="-",
            eta="-",
        )
        rich_ids = {
            id(task): progress.add_task(
                task.remote_path,
                total=max(task.size, 1),
                filename=Path(task.remote_path).name,
                status=task.status.value,
                current_rate="-",
                average_rate="-",
                eta="-",
            )
            for task in tasks
        }

        async def worker(task: TransferTask) -> None:
            task.status = TransferStatus.RUNNING
            meter = Speedometer(alpha=settings.ema_alpha)
            rich_id = rich_ids[id(task)]
            update_progress_fields(progress, rich_id, meter, max(task.size, 1), task.status)
            async with semaphore:
                try:
                    local_path = Path(task.local_path)
                    local_path.parent.mkdir(parents=True, exist_ok=True)
                    resume_from = local_path.stat().st_size if local_path.exists() else 0
                    if resume_from >= task.size and task.size > 0:
                        task.status = TransferStatus.SKIPPED
                        task.transferred = task.size
                        meter.update(task.size)
                        update_progress_fields(progress, rich_id, meter, max(task.size, 1), task.status)
                        return
                    mode = "ab" if resume_from > 0 else "wb"
                    meter.update(resume_from)
                    overall_meter.update(progress.tasks[overall_task].completed + resume_from)
                    with local_path.open(mode) as handle:
                        async for chunk in manager.download_file_stream(task.docid or "", resume_from=resume_from):
                            handle.write(chunk)
                            task.transferred += len(chunk)
                            meter.update(resume_from + task.transferred)
                            update_progress_fields(progress, rich_id, meter, max(task.size, 1), task.status)
                            progress.update(overall_task, advance=len(chunk))
                            overall_meter.update(int(progress.tasks[overall_task].completed))
                            update_progress_fields(progress, overall_task, overall_meter, max(total_bytes, 1), TransferStatus.RUNNING)
                    task.status = TransferStatus.DONE
                    meter.update(task.size)
                    update_progress_fields(progress, rich_id, meter, max(task.size, 1), task.status)
                except Exception as exc:
                    task.status = TransferStatus.FAILED
                    task.error = str(exc)
                    update_progress_fields(progress, rich_id, meter, max(task.size, 1), task.status)

        await asyncio.gather(*(worker(task) for task in tasks))
    return tasks


async def batch_upload(
    manager: "AsyncApiManager",
    tasks: list[TransferTask],
    *,
    jobs: int,
    console: "Console",
) -> list[TransferTask]:
    if not tasks:
        return []
    settings = load_settings()
    semaphore = asyncio.Semaphore(jobs)
    total_bytes = sum(max(task.size, 0) for task in tasks)
    with create_transfer_progress(console, settings.refresh_per_second) as progress:
        overall_meter = Speedometer(alpha=settings.ema_alpha)
        overall_task = progress.add_task(
            "overall",
            total=max(total_bytes, 1),
            filename="overall",
            status=TransferStatus.QUEUED.value,
            current_rate="-",
            average_rate="-",
            eta="-",
        )
        rich_ids = {
            id(task): progress.add_task(
                task.local_path,
                total=max(task.size, 1),
                filename=Path(task.local_path).name,
                status=task.status.value,
                current_rate="-",
                average_rate="-",
                eta="-",
            )
            for task in tasks
        }

        async def worker(task: TransferTask) -> None:
            task.status = TransferStatus.RUNNING
            meter = Speedometer(alpha=settings.ema_alpha)
            rich_id = rich_ids[id(task)]
            update_progress_fields(progress, rich_id, meter, max(task.size, 1), task.status)
            async with semaphore:
                try:
                    local_path = Path(task.local_path)
                    with local_path.open("rb") as handle:
                        class ProgressReader:
                            def __init__(self) -> None:
                                self.transferred = 0

                            async def __aiter__(self):
                                while True:
                                    chunk = handle.read(settings.chunk_size)
                                    if not chunk:
                                        break
                                    self.transferred += len(chunk)
                                    task.transferred = self.transferred
                                    meter.update(self.transferred)
                                    update_progress_fields(progress, rich_id, meter, max(task.size, 1), task.status)
                                    progress.update(overall_task, advance=len(chunk))
                                    overall_meter.update(int(progress.tasks[overall_task].completed))
                                    update_progress_fields(
                                        progress,
                                        overall_task,
                                        overall_meter,
                                        max(total_bytes, 1),
                                        TransferStatus.RUNNING,
                                    )
                                    yield chunk

                        await manager.upload_file(
                            task.docid or "",
                            Path(task.remote_path).name,
                            ProgressReader(),
                            stream_len=task.size,
                            check_existence=True,
                        )
                    task.status = TransferStatus.DONE
                    meter.update(task.size)
                    update_progress_fields(progress, rich_id, meter, max(task.size, 1), task.status)
                except Exception as exc:
                    task.status = TransferStatus.FAILED
                    task.error = str(exc)
                    update_progress_fields(progress, rich_id, meter, max(task.size, 1), task.status)

        await asyncio.gather(*(worker(task) for task in tasks))
    return tasks
