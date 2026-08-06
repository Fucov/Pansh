"""Resolve profile and session policy into an injectable runtime context."""

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from filelock import FileLock

from .config import (
    LEGACY_AUTH_FILE,
    get_auth_dir,
    get_auth_file,
    get_config_dir,
    get_profile_config_file,
    load_profile_config,
    save_profile_config,
    validate_profile_name,
)
from .credentials import CredentialStore, FileCredentialStore, MemoryCredentialStore
from .models import AuthRecord, CachedToken, ProfileConfig, SessionMode


@dataclass
class RuntimeContext:
    profile_name: str
    session_mode: SessionMode
    shared_environment: bool
    profile_config: ProfileConfig
    credential_store: CredentialStore


def resolve_runtime_context(
    settings: Any,
    *,
    profile_name: str | None = None,
    ephemeral: bool = False,
    shared: bool = False,
    config_dir: Path | None = None,
    auth_dir: Path | None = None,
    legacy_auth_files: tuple[Path, ...] | None = None,
) -> RuntimeContext:
    config_root = config_dir or get_config_dir()
    auth_root = auth_dir or get_auth_dir()
    selected_profile = validate_profile_name(
        profile_name
        or os.environ.get("PANSH_PROFILE")
        or str(settings.get("auth.default_profile", "default"))
    )
    shared_environment = (
        shared
        or _env_truthy(os.environ.get("PANSH_SHARED"))
        or str(settings.get("auth.environment", "personal")).lower() == "shared"
    )
    if ephemeral:
        mode = SessionMode.EPHEMERAL
    elif os.environ.get("PANSH_SESSION_MODE"):
        mode = SessionMode(os.environ["PANSH_SESSION_MODE"].lower())
    else:
        configured = str(settings.get("auth.default_mode", "persistent")).lower()
        mode = SessionMode.EPHEMERAL if shared_environment else SessionMode(configured)

    if mode is SessionMode.EPHEMERAL:
        profile = load_profile_config(selected_profile, config_dir=config_root)
        store: CredentialStore = MemoryCredentialStore()
    else:
        path = get_auth_file(selected_profile, auth_dir=auth_root)
        file_store = FileCredentialStore(path)
        if selected_profile == "default":
            sources = (
                legacy_auth_files
                if legacy_auth_files is not None
                else (config_root / "auth.json",)
            )
            if legacy_auth_files is None and config_dir is None:
                sources += (LEGACY_AUTH_FILE,)
            _migrate_legacy_default(config_root, file_store, sources)
        profile = load_profile_config(selected_profile, config_dir=config_root)
        store = file_store

    return RuntimeContext(
        profile_name=selected_profile,
        session_mode=mode,
        shared_environment=shared_environment,
        profile_config=profile,
        credential_store=store,
    )


def _migrate_legacy_default(
    config_dir: Path,
    store: FileCredentialStore,
    sources: tuple[Path, ...],
) -> None:
    store.path.parent.mkdir(parents=True, exist_ok=True)
    if os.name == "posix":
        try:
            store.path.parent.chmod(0o700)
        except OSError:
            pass
    with FileLock(str(store.path) + ".migration.lock"):
        if store.path.exists():
            _retire_legacy_sources(sources)
            return
        legacy = next((path for path in sources if path.exists()), None)
        if legacy is None:
            return
        raw_text = legacy.read_text(encoding="utf-8")
        raw = json.loads(raw_text)
        record = AuthRecord(
            username=raw.get("username"),
            encrypted=raw.get("encrypted"),
            cached_token=CachedToken.model_validate(raw.get("cached_token") or {}),
        )
        profile = ProfileConfig.model_validate(
            {
                key: raw[key]
                for key in ("host", "pubkey", "store_password", "verify_tls")
                if key in raw
            }
        )
        profile_path = get_profile_config_file("default", config_dir=config_dir)
        if not profile_path.exists():
            save_profile_config("default", profile, config_dir=config_dir)
        store.save(record)
        if store.load() != record:
            raise RuntimeError("legacy auth migration verification failed")
        _retire_legacy_sources(sources)


def _retire_legacy_sources(sources: tuple[Path, ...]) -> None:
    for source in sources:
        if not source.exists():
            continue
        backup = source.with_name(source.name + ".bak")
        if not backup.exists():
            _write_legacy_backup(backup, source.read_text(encoding="utf-8"))
        source.unlink()


def _env_truthy(value: str | None) -> bool:
    return value is not None and value.strip().lower() in {"1", "true", "yes", "on"}


def _write_legacy_backup(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            "w",
            encoding="utf-8",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            temporary = Path(handle.name)
            handle.write(content)
            handle.flush()
            try:
                os.fsync(handle.fileno())
            except OSError:
                pass
        if os.name == "posix":
            try:
                temporary.chmod(0o600)
            except OSError:
                pass
        os.replace(temporary, path)
        temporary = None
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
