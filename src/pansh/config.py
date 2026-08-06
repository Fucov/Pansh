"""Dynamic paths and non-sensitive profile configuration."""

from __future__ import annotations

import json
import os
import re
from pathlib import Path

import yaml
from platformdirs import user_config_dir, user_data_dir, user_state_dir

from .models import AppConfig, ProfileConfig

APP_NAME = "pansh"
LEGACY_APP_NAME = "bhpan"
ENV_CONFIG_PATH = "PANSH_CONFIG"
LEGACY_ENV_CONFIG_PATH = "pansh_CONFIG"
ENV_AUTH_DIR = "PANSH_AUTH_DIR"
PROFILE_PATTERN = re.compile(r"^[A-Za-z0-9._-]+$")


def get_config_dir() -> Path:
    override = os.environ.get(ENV_CONFIG_PATH) or os.environ.get(LEGACY_ENV_CONFIG_PATH)
    if override:
        path = Path(override).expanduser().resolve()
        if path.suffix:
            return path.parent
        return path
    return Path(user_config_dir(APP_NAME))


def get_data_dir() -> Path:
    return Path(user_data_dir(APP_NAME))


def get_auth_dir() -> Path:
    override = os.environ.get(ENV_AUTH_DIR)
    if override:
        return Path(override).expanduser().resolve()
    return Path(user_state_dir(APP_NAME))


def validate_profile_name(name: str) -> str:
    if name in {"", ".", ".."} or PROFILE_PATTERN.fullmatch(name) is None:
        raise ValueError("profile 名只允许字母、数字、点、下划线和连字符")
    return name


def get_profile_config_file(name: str, *, config_dir: Path | None = None) -> Path:
    profile = validate_profile_name(name)
    root = config_dir or get_config_dir()
    return root / "profiles" / profile / "profile.yaml"


def get_auth_file(name: str, *, auth_dir: Path | None = None) -> Path:
    profile = validate_profile_name(name)
    root = auth_dir or get_auth_dir()
    return root / "profiles" / profile / "auth.json"


def load_profile_config(name: str, *, config_dir: Path | None = None) -> ProfileConfig:
    path = get_profile_config_file(name, config_dir=config_dir)
    if not path.exists():
        return ProfileConfig()
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return ProfileConfig.model_validate(raw)


def save_profile_config(
    name: str,
    profile: ProfileConfig,
    *,
    config_dir: Path | None = None,
) -> Path:
    path = get_profile_config_file(name, config_dir=config_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        yaml.safe_dump(profile.model_dump(mode="json"), allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )
    return path


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
    """Compatibility writer for the pre-profile configuration format."""
    ensure_runtime_dirs()
    payload = cfg.model_dump(mode="json")
    payload["revision"] = _CURRENT_REVISION
    AUTH_FILE.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
