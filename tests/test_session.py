from __future__ import annotations

import asyncio

from pansh.main import AppState
from pansh.credentials import MemoryCredentialStore
from pansh.models import AuthRecord, ProfileConfig, SessionMode
from pansh.runtime import RuntimeContext
from pansh.session import Session, SessionController
from pansh.theme import UIOptions


class DummyManager:
    def __init__(self, token: str = "token", expires_at: float = 3600.0) -> None:
        self._tokenid = token
        self._expires = expires_at
        self.initialize_calls = 0
        self.close_calls = 0

    async def initialize(self) -> None:
        self.initialize_calls += 1

    async def close(self) -> None:
        self.close_calls += 1


class DummyConsole:
    def input(self, prompt: str) -> str:
        return "user"

    def status(self, message: str):
        class _Status:
            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                return False

        return _Status()

    def print(self, *args, **kwargs) -> None:
        return None


def _state(*, once: bool) -> AppState:
    runtime = RuntimeContext(
        profile_name="default",
        session_mode=SessionMode.EPHEMERAL if once else SessionMode.PERSISTENT,
        shared_environment=False,
        profile_config=ProfileConfig(),
        credential_store=MemoryCredentialStore(),
    )
    return AppState(
        ui=UIOptions(),
        console=DummyConsole(),
        stderr_console=DummyConsole(),
        settings=None,
        runtime_context=runtime,
        session_controller=SessionController(runtime),
    )


def _session(mode: str, manager: DummyManager | None = None) -> Session:
    current_manager = manager or DummyManager()
    return Session(
        mode=mode,
        host="example.test",
        username="user",
        token=current_manager._tokenid,
        expires_at=current_manager._expires,
        home_path="/home",
        manager=current_manager,
        created_at=1.0,
        pid=123,
    )


def test_once_session_reuses_same_login(monkeypatch) -> None:
    state = _state(once=True)
    controller = state.session_controller
    assert controller is not None
    created = 0

    async def fake_create_session(*, state, console, force_reauth=False):
        nonlocal created
        created += 1
        session = _session("ephemeral")
        controller.session = session
        state.session = session
        return session

    async def fake_refresh_session(*, state):
        assert controller.session is not None
        return controller.session

    monkeypatch.setattr(controller, "create_session", fake_create_session)
    monkeypatch.setattr(controller, "refresh_session", fake_refresh_session)

    async def runner() -> None:
        first = await controller.require_session(state=state, console=state.console)
        second = await controller.require_session(state=state, console=state.console)
        assert first is second

    asyncio.run(runner())
    assert created == 1


def test_once_session_close_invalidates_session() -> None:
    state = _state(once=True)
    controller = state.session_controller
    assert controller is not None
    manager = DummyManager()
    session = _session("ephemeral", manager)
    controller.session = session
    state.session = session

    asyncio.run(controller.close(state=state))

    assert controller.session is None
    assert state.session is None
    assert manager.close_calls == 1


def test_persistent_refresh_updates_saved_token() -> None:
    state = _state(once=False)
    controller = state.session_controller
    assert controller is not None
    manager = DummyManager(token="fresh-token", expires_at=7200.0)
    session = _session("persistent", manager)
    controller.session = session
    state.session = session
    asyncio.run(controller.refresh_session(state=state))

    record = state.runtime_context.credential_store.load()
    assert record.cached_token.token == "fresh-token"
    assert record.cached_token.expires == 7200.0


def test_logout_differs_for_ephemeral_and_persistent() -> None:
    persistent_state = _state(once=False)
    persistent_controller = persistent_state.session_controller
    assert persistent_controller is not None
    persistent_manager = DummyManager()
    persistent_session = _session("persistent", persistent_manager)
    persistent_controller.session = persistent_session
    persistent_state.session = persistent_session

    ephemeral_state = _state(once=True)
    ephemeral_controller = ephemeral_state.session_controller
    assert ephemeral_controller is not None
    ephemeral_manager = DummyManager()
    ephemeral_session = _session("ephemeral", ephemeral_manager)
    ephemeral_controller.session = ephemeral_session
    ephemeral_state.session = ephemeral_session

    persistent_store = persistent_state.runtime_context.credential_store
    ephemeral_store = ephemeral_state.runtime_context.credential_store
    persistent_store.save(AuthRecord(username="saved-user", encrypted="cipher"))
    ephemeral_store.save(AuthRecord(username="temporary-user"))

    asyncio.run(ephemeral_controller.logout(state=ephemeral_state))
    assert ephemeral_store.load().username is None
    assert persistent_store.load().username == "saved-user"
    assert ephemeral_controller.session is None
    assert ephemeral_state.session is None

    asyncio.run(persistent_controller.logout(state=persistent_state))
    assert persistent_store.load().username is None
    assert persistent_controller.session is None
    assert persistent_state.session is None
