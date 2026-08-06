from __future__ import annotations

import json
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from pansh.credentials import FileCredentialStore, MemoryCredentialStore
from pansh.models import AuthRecord, SessionMode
from pansh import runtime as runtime_module
from pansh.runtime import resolve_runtime_context


class DummySettings:
    def __init__(self, values: dict[str, str] | None = None) -> None:
        self.values = values or {}

    def get(self, key: str, default=None):
        return self.values.get(key, default)


def test_shared_environment_defaults_to_ephemeral(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("PANSH_SHARED", "1")

    runtime = resolve_runtime_context(
        DummySettings(), config_dir=tmp_path / "config", auth_dir=tmp_path / "state"
    )

    assert runtime.shared_environment is True
    assert runtime.session_mode is SessionMode.EPHEMERAL
    assert isinstance(runtime.credential_store, MemoryCredentialStore)


def test_cli_mode_overrides_environment_and_settings(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("PANSH_SESSION_MODE", "persistent")

    runtime = resolve_runtime_context(
        DummySettings({"auth.default_mode": "persistent"}),
        ephemeral=True,
        config_dir=tmp_path / "config",
        auth_dir=tmp_path / "state",
    )

    assert runtime.session_mode is SessionMode.EPHEMERAL
    assert isinstance(runtime.credential_store, MemoryCredentialStore)


def test_profile_cli_overrides_environment(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("PANSH_PROFILE", "environment")

    runtime = resolve_runtime_context(
        DummySettings(),
        profile_name="command-line",
        config_dir=tmp_path / "config",
        auth_dir=tmp_path / "state",
    )

    assert runtime.profile_name == "command-line"
    assert isinstance(runtime.credential_store, FileCredentialStore)


def test_ephemeral_never_reads_or_migrates_legacy_auth(tmp_path: Path) -> None:
    legacy = tmp_path / "config" / "auth.json"
    legacy.parent.mkdir(parents=True)
    legacy.write_text(
        json.dumps(
            {
                "username": "other-user",
                "encrypted": "secret-ciphertext",
                "cached_token": {"token": "secret-token", "expires": 9999999999},
            }
        ),
        encoding="utf-8",
    )

    runtime = resolve_runtime_context(
        DummySettings(),
        ephemeral=True,
        config_dir=tmp_path / "config",
        auth_dir=tmp_path / "state",
    )

    assert runtime.credential_store.load().username is None
    assert legacy.exists()
    assert not legacy.with_suffix(".json.bak").exists()
    assert not (tmp_path / "state" / "profiles" / "default" / "auth.json").exists()


def test_parallel_ephemeral_sessions_have_independent_credentials(tmp_path: Path) -> None:
    contexts = [
        resolve_runtime_context(
            DummySettings(),
            ephemeral=True,
            config_dir=tmp_path / "config",
            auth_dir=tmp_path / "state",
        )
        for _ in range(2)
    ]

    with ThreadPoolExecutor(max_workers=2) as executor:
        list(
            executor.map(
                lambda item: item[0].credential_store.save(
                    AuthRecord(username=item[1])
                ),
                zip(contexts, ("alice", "bob")),
            )
        )

    assert contexts[0].credential_store is not contexts[1].credential_store
    assert [context.credential_store.load().username for context in contexts] == [
        "alice",
        "bob",
    ]
    assert not (tmp_path / "state" / "profiles" / "default" / "auth.json").exists()


def test_legacy_auth_migrates_once_for_persistent_default(tmp_path: Path) -> None:
    config_dir = tmp_path / "config"
    auth_dir = tmp_path / "state"
    legacy = config_dir / "auth.json"
    legacy.parent.mkdir(parents=True)
    legacy.write_text(
        json.dumps(
            {
                "revision": 5,
                "host": "legacy.test",
                "username": "legacy-user",
                "encrypted": "ciphertext",
                "cached_token": {"token": "old-token", "expires": 1234},
                "verify_tls": False,
            }
        ),
        encoding="utf-8",
    )

    runtime = resolve_runtime_context(
        DummySettings(), config_dir=config_dir, auth_dir=auth_dir
    )

    assert runtime.profile_config.host == "legacy.test"
    assert runtime.profile_config.verify_tls is False
    assert runtime.credential_store.load().username == "legacy-user"
    assert not legacy.exists()
    assert (config_dir / "auth.json.bak").exists()

    runtime.credential_store.save(runtime.credential_store.load().model_copy(update={"username": "new-user"}))
    legacy.write_text(json.dumps({"username": "must-not-overwrite"}), encoding="utf-8")

    second = resolve_runtime_context(
        DummySettings(), config_dir=config_dir, auth_dir=auth_dir
    )
    assert second.credential_store.load().username == "new-user"


def test_non_default_profile_does_not_migrate_legacy_auth(tmp_path: Path) -> None:
    legacy = tmp_path / "config" / "auth.json"
    legacy.parent.mkdir(parents=True)
    legacy.write_text(json.dumps({"username": "legacy-user"}), encoding="utf-8")

    runtime = resolve_runtime_context(
        DummySettings(),
        profile_name="work",
        config_dir=tmp_path / "config",
        auth_dir=tmp_path / "state",
    )

    assert runtime.credential_store.load().username is None
    assert legacy.exists()


def test_bhpan_legacy_config_migrates_to_default_profile(tmp_path: Path) -> None:
    legacy = tmp_path / "bhpan" / "config.json"
    legacy.parent.mkdir(parents=True)
    legacy.write_text(
        json.dumps(
            {
                "host": "old-bhpan.test",
                "username": "old-user",
                "encrypted": "old-cipher",
            }
        ),
        encoding="utf-8",
    )

    runtime = resolve_runtime_context(
        DummySettings(),
        config_dir=tmp_path / "config",
        auth_dir=tmp_path / "state",
        legacy_auth_files=(legacy,),
    )

    assert runtime.profile_config.host == "old-bhpan.test"
    assert runtime.credential_store.load().username == "old-user"
    assert not legacy.exists()
    assert legacy.with_name("config.json.bak").exists()


def test_all_legacy_sources_are_retired_after_migration(tmp_path: Path) -> None:
    config_dir = tmp_path / "config"
    auth_dir = tmp_path / "state"
    current_legacy = config_dir / "auth.json"
    stale_legacy = tmp_path / "bhpan" / "config.json"
    current_legacy.parent.mkdir(parents=True)
    stale_legacy.parent.mkdir(parents=True)
    current_legacy.write_text(json.dumps({"username": "current"}), encoding="utf-8")
    stale_legacy.write_text(json.dumps({"username": "stale"}), encoding="utf-8")

    runtime = resolve_runtime_context(
        DummySettings(),
        config_dir=config_dir,
        auth_dir=auth_dir,
        legacy_auth_files=(current_legacy, stale_legacy),
    )
    assert runtime.credential_store.load().username == "current"

    runtime.credential_store.clear()
    second = resolve_runtime_context(
        DummySettings(),
        config_dir=config_dir,
        auth_dir=auth_dir,
        legacy_auth_files=(current_legacy, stale_legacy),
    )

    assert second.credential_store.load().username is None
    assert not current_legacy.exists()
    assert not stale_legacy.exists()
    assert current_legacy.with_name("auth.json.bak").exists()
    assert stale_legacy.with_name("config.json.bak").exists()


def test_concurrent_legacy_migration_is_idempotent(monkeypatch, tmp_path: Path) -> None:
    config_dir = tmp_path / "config"
    auth_dir = tmp_path / "state"
    legacy = config_dir / "auth.json"
    legacy.parent.mkdir(parents=True)
    legacy.write_text(json.dumps({"username": "legacy-user"}), encoding="utf-8")
    original_backup = runtime_module._write_legacy_backup

    def slow_backup(path: Path, content: str) -> None:
        time.sleep(0.05)
        original_backup(path, content)

    monkeypatch.setattr(runtime_module, "_write_legacy_backup", slow_backup)

    def resolve_username(_: int) -> str | None:
        context = resolve_runtime_context(
            DummySettings(), config_dir=config_dir, auth_dir=auth_dir
        )
        return context.credential_store.load().username

    with ThreadPoolExecutor(max_workers=8) as executor:
        usernames = list(executor.map(resolve_username, range(8)))

    assert usernames == ["legacy-user"] * 8
    assert not legacy.exists()
