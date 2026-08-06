from __future__ import annotations

import asyncio
from pathlib import Path

from pansh.credentials import FileCredentialStore, MemoryCredentialStore
from pansh.models import AuthRecord, ProfileConfig, SessionMode
from pansh.runtime import RuntimeContext
from pansh.session import RuntimeSession, SessionController, SessionState


class DummyConsole:
    def __init__(self, username: str = "current-user") -> None:
        self.username = username

    def input(self, prompt: str) -> str:
        return self.username

    def status(self, message: str):
        class Status:
            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                return False

        return Status()

    def print(self, *args, **kwargs) -> None:
        return None


class DummyManager:
    def __init__(self, username: str, *, token: str = "new-token") -> None:
        self._username = username
        self._tokenid = token
        self._expires = 7200.0
        self.initialize_calls = 0
        self.close_calls = 0

    async def initialize(self) -> None:
        self.initialize_calls += 1

    async def get_entrydoc(self):
        return [{"name": "home"}]

    async def close(self) -> None:
        self.close_calls += 1


class DummyState:
    session = None


def _runtime(mode: SessionMode, store) -> RuntimeContext:
    return RuntimeContext(
        profile_name="work1",
        session_mode=mode,
        shared_environment=mode is SessionMode.EPHEMERAL,
        profile_config=ProfileConfig(host="example.test"),
        credential_store=store,
    )


def test_ephemeral_login_uses_current_input_and_never_touches_file_store(
    monkeypatch, tmp_path: Path
) -> None:
    disk = FileCredentialStore(tmp_path / "profiles" / "work1" / "auth.json")
    disk.save(AuthRecord(username="other-user", encrypted="other-cipher"))
    monkeypatch.setattr(FileCredentialStore, "load", lambda self: (_ for _ in ()).throw(AssertionError("disk load")))
    monkeypatch.setattr(FileCredentialStore, "save", lambda self, record: (_ for _ in ()).throw(AssertionError("disk save")))
    monkeypatch.setattr(FileCredentialStore, "clear", lambda self: (_ for _ in ()).throw(AssertionError("disk clear")))
    monkeypatch.setattr("pansh.session.getpass.getpass", lambda prompt: "current-password")
    monkeypatch.setattr("pansh.session.rsa_encrypt", lambda password, pubkey: f"encrypted:{password}")
    captured: dict[str, object] = {}

    def manager_factory(host, username, password, pubkey, **kwargs):
        captured.update(
            host=host,
            username=username,
            password=password,
            encrypted=kwargs.get("encrypted"),
            cached_token=kwargs.get("cached_token"),
            verify_tls=kwargs.get("verify_tls"),
        )
        return DummyManager(username)

    store = MemoryCredentialStore()
    controller = SessionController(
        _runtime(SessionMode.EPHEMERAL, store), manager_factory=manager_factory
    )
    state = DummyState()

    session = asyncio.run(controller.create_session(state=state, console=DummyConsole()))

    assert session.state.username == "current-user"
    assert captured == {
        "host": "example.test",
        "username": "current-user",
        "password": "current-password",
        "encrypted": "encrypted:current-password",
        "cached_token": None,
        "verify_tls": True,
    }
    assert store.load().username == "current-user"
    assert store.load().cached_token.token == "new-token"


def test_ephemeral_shell_state_reuses_one_manager(monkeypatch) -> None:
    monkeypatch.setattr("pansh.session.getpass.getpass", lambda prompt: "password")
    monkeypatch.setattr("pansh.session.rsa_encrypt", lambda password, pubkey: "cipher")
    managers: list[DummyManager] = []

    def manager_factory(host, username, password, pubkey, **kwargs):
        manager = DummyManager(username)
        managers.append(manager)
        return manager

    controller = SessionController(
        _runtime(SessionMode.EPHEMERAL, MemoryCredentialStore()),
        manager_factory=manager_factory,
    )
    state = DummyState()

    async def runner() -> None:
        first = await controller.require_session(state=state, console=DummyConsole())
        second = await controller.require_session(state=state, console=DummyConsole())
        assert first.manager is second.manager
        await controller.close(state=state)

    asyncio.run(runner())

    assert len(managers) == 1
    assert managers[0].close_calls == 1


def test_persistent_refresh_updates_injected_store() -> None:
    store = MemoryCredentialStore()
    store.save(AuthRecord(username="saved-user", encrypted="saved-cipher"))
    manager = DummyManager("saved-user", token="fresh-token")

    def manager_factory(host, username, password, pubkey, **kwargs):
        return manager

    controller = SessionController(
        _runtime(SessionMode.PERSISTENT, store), manager_factory=manager_factory
    )
    state = DummyState()

    async def runner() -> None:
        await controller.create_session(state=state, console=DummyConsole())
        manager._tokenid = "refreshed-token"
        await controller.refresh_session(state=state)

    asyncio.run(runner())

    assert store.load().cached_token.token == "refreshed-token"


def test_profile_token_refresh_does_not_update_another_profile(tmp_path: Path) -> None:
    alpha_store = FileCredentialStore(tmp_path / "alpha" / "auth.json")
    beta_store = FileCredentialStore(tmp_path / "beta" / "auth.json")
    alpha_store.save(AuthRecord(username="alice"))
    beta_store.save(
        AuthRecord(
            username="bob",
            cached_token={"token": "beta-token", "expires": 9000},
        )
    )
    manager = DummyManager("alice", token="alpha-token")
    controller = SessionController(_runtime(SessionMode.PERSISTENT, alpha_store))
    state = DummyState()
    session = RuntimeSession(
        state=SessionState(
            mode=SessionMode.PERSISTENT,
            profile_name="work1",
            host="example.test",
            username="alice",
            token="old-alpha-token",
            expires_at=1000,
            home_path="/home",
            created_at=1,
            pid=1,
        ),
        manager=manager,
    )
    controller.runtime_session = session
    state.runtime_session = session

    asyncio.run(controller.refresh_session(state=state))

    assert alpha_store.load().cached_token.token == "alpha-token"
    assert beta_store.load().cached_token.token == "beta-token"


def test_ephemeral_logout_only_clears_memory_store(tmp_path: Path) -> None:
    persistent = FileCredentialStore(tmp_path / "persistent" / "auth.json")
    persistent.save(AuthRecord(username="saved-user"))
    ephemeral = MemoryCredentialStore()
    ephemeral.save(AuthRecord(username="temporary-user"))
    controller = SessionController(_runtime(SessionMode.EPHEMERAL, ephemeral))

    asyncio.run(controller.logout(state=DummyState()))

    assert ephemeral.load().username is None
    assert persistent.load().username == "saved-user"


def test_secret_models_do_not_expose_credentials_in_repr() -> None:
    record = AuthRecord(
        username="alice",
        encrypted="encrypted-secret",
        cached_token={"token": "token-secret", "expires": 1},
    )

    rendered = repr(record)

    assert "encrypted-secret" not in rendered
    assert "token-secret" not in rendered
