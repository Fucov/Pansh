from __future__ import annotations

import asyncio
import io
from types import SimpleNamespace

from rich.console import Console
from rich.progress import Progress

from pansh.models import TransferStatus, TransferTask
from pansh.transfer import batch_download


class RecordingProgress(Progress):
    def __init__(self) -> None:
        super().__init__(
            console=Console(file=io.StringIO(), force_terminal=False),
            auto_refresh=False,
        )
        self.peak_tasks = 0

    def add_task(self, *args, **kwargs):
        task_id = super().add_task(*args, **kwargs)
        self.peak_tasks = max(self.peak_tasks, len(self.tasks))
        return task_id


class FakeDownloadManager:
    def __init__(self) -> None:
        self.active = 0
        self.peak_active = 0

    async def download_file_stream(self, file_id: str, *, resume_from: int = 0):
        assert resume_from == 0
        self.active += 1
        self.peak_active = max(self.peak_active, self.active)
        try:
            await asyncio.sleep(0)
            yield b"x"
        finally:
            self.active -= 1


def test_batch_download_bounds_live_progress_rows(monkeypatch, tmp_path) -> None:
    jobs = 3
    task_count = 24
    progress = RecordingProgress()
    manager = FakeDownloadManager()
    tasks = [
        TransferTask(
            remote_path=f"/remote/{index}.bin",
            local_path=str(tmp_path / f"{index}.bin"),
            size=1,
            docid=str(index),
        )
        for index in range(task_count)
    ]
    settings = SimpleNamespace(
        refresh_per_second=6,
        ema_alpha=0.25,
    )
    monkeypatch.setattr(
        "pansh.transfer.create_transfer_progress",
        lambda console, refresh_per_second: progress,
    )
    monkeypatch.setattr("pansh.transfer.load_settings", lambda: settings)

    asyncio.run(
        batch_download(
            manager,
            tasks,
            jobs=jobs,
            console=Console(file=io.StringIO(), force_terminal=False),
        )
    )

    assert progress.peak_tasks <= jobs + 1
    assert len(progress.tasks) == 1
    assert manager.peak_active <= jobs
    assert all(task.status == TransferStatus.DONE for task in tasks)
    assert all(
        (tmp_path / f"{index}.bin").read_bytes() == b"x"
        for index in range(task_count)
    )
