from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from pansh.config import (
    get_auth_file,
    get_profile_config_file,
    load_profile_config,
    validate_profile_name,
)
from pansh.credentials import FileCredentialStore, MemoryCredentialStore
from pansh.models import AuthRecord, CachedToken


@pytest.mark.parametrize(
    "name",
    ["", "..", ".", "/tmp/profile", "../escape", "a/b", "a\\b", "white space"],
)
def test_profile_name_rejects_path_traversal(name: str) -> None:
    with pytest.raises(ValueError, match="profile"):
        validate_profile_name(name)


def test_profile_paths_are_isolated(tmp_path: Path) -> None:
    assert get_profile_config_file("alpha", config_dir=tmp_path) != get_profile_config_file(
        "beta", config_dir=tmp_path
    )
    assert get_auth_file("alpha", auth_dir=tmp_path) != get_auth_file("beta", auth_dir=tmp_path)


def test_memory_store_starts_empty() -> None:
    store = MemoryCredentialStore()

    assert store.load() == AuthRecord()
    assert store.describe() == "memory"


def test_new_file_store_loads_as_empty_without_creating_auth_file(tmp_path: Path) -> None:
    store = FileCredentialStore(tmp_path / "missing" / "auth.json")

    assert store.load() == AuthRecord()
    assert not store.path.exists()


def test_file_stores_keep_profiles_separate(tmp_path: Path) -> None:
    alpha = FileCredentialStore(get_auth_file("alpha", auth_dir=tmp_path))
    beta = FileCredentialStore(get_auth_file("beta", auth_dir=tmp_path))

    alpha.save(AuthRecord(username="alice"))
    beta.save(AuthRecord(username="bob"))

    assert alpha.load().username == "alice"
    assert beta.load().username == "bob"

    alpha.clear()
    assert alpha.load().username is None
    assert beta.load().username == "bob"


def test_concurrent_file_store_writes_leave_complete_json(tmp_path: Path) -> None:
    path = get_auth_file("shared", auth_dir=tmp_path)

    def save(index: int) -> None:
        FileCredentialStore(path).save(
            AuthRecord(
                username=f"user-{index}",
                cached_token=CachedToken(token=f"token-{index}", expires=float(index)),
            )
        )

    with ThreadPoolExecutor(max_workers=8) as executor:
        list(executor.map(save, range(40)))

    raw = json.loads(path.read_text(encoding="utf-8"))
    record = AuthRecord.model_validate(raw)
    assert record.username is not None
    assert record.cached_token.token.startswith("token-")


def test_failed_atomic_replace_preserves_previous_record(monkeypatch, tmp_path: Path) -> None:
    path = get_auth_file("stable", auth_dir=tmp_path)
    store = FileCredentialStore(path)
    store.save(AuthRecord(username="before"))

    def fail_replace(source, destination) -> None:
        raise OSError("replace failed")

    monkeypatch.setattr("pansh.credentials.os.replace", fail_replace)

    with pytest.raises(OSError, match="replace failed"):
        store.save(AuthRecord(username="after"))

    assert json.loads(path.read_text(encoding="utf-8"))["username"] == "before"
    assert list(path.parent.glob(".auth.json.*.tmp")) == []


def test_ephemeral_profile_can_read_connection_config_only(tmp_path: Path) -> None:
    profile_file = get_profile_config_file("work1", config_dir=tmp_path)
    profile_file.parent.mkdir(parents=True)
    profile_file.write_text("host: example.test\nverify_tls: false\n", encoding="utf-8")

    profile = load_profile_config("work1", config_dir=tmp_path)

    assert profile.host == "example.test"
    assert profile.verify_tls is False
    assert not hasattr(profile, "username")
