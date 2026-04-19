"""YAML settings management for pansh."""

from __future__ import annotations

import os
from functools import lru_cache
from importlib.resources import files
from pathlib import Path
from typing import Any

import yaml

from .config import ENV_CONFIG_PATH, LEGACY_ENV_CONFIG_PATH, get_config_dir

ENV_SETTINGS_PATH = ENV_CONFIG_PATH


def default_settings_text() -> str:
    return (
        files("pansh")
        .joinpath("defaults/settings.yaml")
        .read_text(encoding="utf-8")
    )


def get_settings_path() -> Path:
    override = os.environ.get(ENV_SETTINGS_PATH) or os.environ.get(LEGACY_ENV_CONFIG_PATH)
    if override:
        return Path(override).expanduser().resolve()
    return get_config_dir() / "settings.yaml"


def ensure_settings_file() -> Path:
    path = get_settings_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        path.write_text(default_settings_text(), encoding="utf-8")
    return path


class Settings:
    """Thin wrapper around the YAML settings document."""

    def __init__(self, path: Path | None = None) -> None:
        self.path = path or ensure_settings_file()
        self.raw: dict[str, Any] = {}
        self.reload()

    def reload(self) -> None:
        ensure_settings_file()
        self.raw = yaml.safe_load(self.path.read_text(encoding="utf-8")) or {}
        self._apply_env_overrides()

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(
            yaml.safe_dump(
                self.raw,
                allow_unicode=True,
                sort_keys=False,
                default_flow_style=False,
            ),
            encoding="utf-8",
        )

    def get(self, key: str, default: Any = None) -> Any:
        value: Any = self.raw
        for chunk in key.split("."):
            if not isinstance(value, dict):
                return default
            value = value.get(chunk)
            if value is None:
                return default
        return value

    def set(self, key: str, value: Any) -> None:
        cursor = self.raw
        parts = key.split(".")
        for part in parts[:-1]:
            cursor = cursor.setdefault(part, {})
        cursor[parts[-1]] = value

    def _apply_env_overrides(self) -> None:
        theme = os.environ.get("PANSH_THEME") or os.environ.get("pansh_THEME")
        if theme:
            self.set("theme.mode", theme)
        jobs = os.environ.get("PANSH_JOBS") or os.environ.get("pansh_JOBS")
        if jobs:
            self.set("transfer.default_jobs", int(jobs))

    @property
    def theme_mode(self) -> str:
        return str(self.get("theme.mode", "auto"))

    @property
    def default_jobs(self) -> int:
        return int(self.get("transfer.default_jobs", 4))

    @property
    def chunk_size(self) -> int:
        return int(self.get("transfer.chunk_size", 65536))

    @property
    def refresh_per_second(self) -> int:
        return int(self.get("transfer.refresh_per_second", 6))

    @property
    def ema_alpha(self) -> float:
        return float(self.get("transfer.ema_alpha", 0.25))

    @property
    def connect_timeout(self) -> float:
        return float(self.get("network.connect_timeout", 5.0))

    @property
    def read_timeout(self) -> float:
        return float(self.get("network.read_timeout", 30.0))

    @property
    def request_timeout(self) -> float:
        return float(self.get("network.request_timeout", 30.0))

    @property
    def max_retries(self) -> int:
        return int(self.get("network.max_retries", 3))

    @property
    def retry_backoff(self) -> float:
        return float(self.get("network.retry_backoff", 1.5))

    @property
    def search_depth(self) -> int:
        return int(self.get("search.default_depth", 3))

    @property
    def max_depth(self) -> int:
        return int(self.get("search.max_depth", 10))


@lru_cache(maxsize=1)
def load_settings() -> Settings:
    return Settings()


def reload_settings() -> Settings:
    load_settings.cache_clear()
    return load_settings()
