"""Persistent path and auth configuration helpers."""

from __future__ import annotations

import json
import os
from pathlib import Path

from platformdirs import user_config_dir, user_data_dir

from .models import AppConfig

APP_NAME = "pancli"
LEGACY_APP_NAME = "bhpan"


def get_config_dir() -> Path:
    override = os.environ.get("PANCLI_CONFIG")
    if override:
        path = Path(override).expanduser().resolve()
        if path.suffix:
            return path.parent
        return path
    return Path(user_config_dir(APP_NAME))


def get_data_dir() -> Path:
    return Path(user_data_dir(APP_NAME))


def ensure_runtime_dirs() -> tuple[Path, Path]:
    config_dir = get_config_dir()
    data_dir = get_data_dir()
    config_dir.mkdir(parents=True, exist_ok=True)
    data_dir.mkdir(parents=True, exist_ok=True)
    return config_dir, data_dir


CONFIG_DIR = get_config_dir()
DATA_DIR = get_data_dir()
AUTH_FILE = CONFIG_DIR / "auth.json"
LEGACY_AUTH_FILE = Path(user_config_dir(LEGACY_APP_NAME)) / "config.json"
CERT_FILE = DATA_DIR / "missing_cert.pem"

_CURRENT_REVISION = 5


def _migrate_config(raw: dict) -> dict:
    revision = int(raw.get("revision", 0) or 0)
    if revision < 4:
        raw.setdefault("theme", "auto")
    if revision < 5:
        raw.setdefault("verify_tls", True)
    raw["revision"] = _CURRENT_REVISION
    return raw


def load_config() -> AppConfig:
    ensure_runtime_dirs()
    if AUTH_FILE.exists():
        raw = json.loads(AUTH_FILE.read_text(encoding="utf-8"))
        return AppConfig.model_validate(_migrate_config(raw))
    if LEGACY_AUTH_FILE.exists():
        raw = json.loads(LEGACY_AUTH_FILE.read_text(encoding="utf-8"))
        cfg = AppConfig.model_validate(_migrate_config(raw))
        save_config(cfg)
        return cfg
    return AppConfig(revision=_CURRENT_REVISION)


def save_config(cfg: AppConfig) -> None:
    ensure_runtime_dirs()
    payload = cfg.model_dump(mode="json")
    payload["revision"] = _CURRENT_REVISION
    AUTH_FILE.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
