"""Transfer progress helpers with smoothed speed and ETA."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

from rich.console import Console
from rich.progress import (
    BarColumn,
    Progress,
    ProgressColumn,
    SpinnerColumn,
    Task,
    TaskProgressColumn,
    TextColumn,
)

from .models import TransferStatus


def format_bytes(num: float) -> str:
    units = ["B", "KB", "MB", "GB", "TB", "PB"]
    value = float(num)
    for unit in units:
        if abs(value) < 1024 or unit == units[-1]:
            return f"{value:.1f}{unit}"
        value /= 1024
    return f"{value:.1f}EB"


def format_rate(num: float) -> str:
    if num <= 0:
        return "-"
    return f"{format_bytes(num)}/s"


def format_eta(seconds: float | None) -> str:
    if seconds is None or seconds < 0 or seconds == float("inf"):
        return "-"
    seconds = int(seconds)
    minutes, sec = divmod(seconds, 60)
    hours, minutes = divmod(minutes, 60)
    if hours:
        return f"{hours:d}:{minutes:02d}:{sec:02d}"
    return f"{minutes:02d}:{sec:02d}"


@dataclass(slots=True)
class Speedometer:
    alpha: float = 0.25
    started_at: float = field(default_factory=time.perf_counter)
    last_at: float = field(default_factory=time.perf_counter)
    last_bytes: int = 0
    transferred: int = 0
    current_speed: float = 0.0

    def update(self, transferred: int, *, now: float | None = None) -> None:
        now = time.perf_counter() if now is None else now
        delta_time = max(now - self.last_at, 1e-6)
        delta_bytes = max(transferred - self.last_bytes, 0)
        instant = delta_bytes / delta_time if delta_bytes else 0.0
        self.current_speed = (
            instant
            if self.current_speed == 0
            else (self.alpha * instant + (1 - self.alpha) * self.current_speed)
        )
        self.transferred = transferred
        self.last_bytes = transferred
        self.last_at = now

    @property
    def average_speed(self) -> float:
        elapsed = max(self.last_at - self.started_at, 1e-6)
        return self.transferred / elapsed

    def eta(self, total: int) -> float | None:
        remaining = max(total - self.transferred, 0)
        if remaining == 0:
            return 0.0
        speed = self.current_speed or self.average_speed
        if speed <= 0:
            return None
        return remaining / speed


class StatusColumn(ProgressColumn):
    def render(self, task: Task) -> str:
        return str(task.fields.get("status", TransferStatus.QUEUED.value))


class RateColumn(ProgressColumn):
    def __init__(self, field_name: str) -> None:
        super().__init__()
        self.field_name = field_name

    def render(self, task: Task) -> str:
        return str(task.fields.get(self.field_name, "-"))


class SizeColumn(ProgressColumn):
    def render(self, task: Task) -> str:
        total = task.total or 0
        return f"{format_bytes(task.completed)} / {format_bytes(total)}"


class EtaColumn(ProgressColumn):
    def render(self, task: Task) -> str:
        return str(task.fields.get("eta", "-"))


def create_transfer_progress(console: Console, refresh_per_second: int = 6) -> Progress:
    return Progress(
        SpinnerColumn(),
        TextColumn("{task.fields[filename]}", justify="left"),
        BarColumn(bar_width=24),
        TaskProgressColumn(),
        SizeColumn(),
        TextColumn("cur"),
        RateColumn("current_rate"),
        TextColumn("avg"),
        RateColumn("average_rate"),
        TextColumn("eta"),
        EtaColumn(),
        TextColumn("{task.fields[status]}", justify="left"),
        console=console,
        refresh_per_second=refresh_per_second,
    )


def update_progress_fields(
    progress: Progress,
    task_id: Any,
    meter: Speedometer,
    total: int,
    status: TransferStatus,
) -> None:
    progress.update(
        task_id,
        completed=meter.transferred,
        status=status.value,
        current_rate=format_rate(meter.current_speed),
        average_rate=format_rate(meter.average_speed),
        eta=format_eta(meter.eta(total)),
    )
