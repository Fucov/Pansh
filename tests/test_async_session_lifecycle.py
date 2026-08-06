from __future__ import annotations

import asyncio
from dataclasses import fields

from pansh.credentials import MemoryCredentialStore
from pansh.models import ProfileConfig, SessionMode
from pansh.runtime import RuntimeContext
from pansh.session import RuntimeSession, SessionController, SessionState


class DummyConsole:
    def input(self, prompt: str) -> str:
        return "alice"

    def status(self, message: str):
        class Status:
            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                return False

        return Status()

    def print(self, *args, **kwargs) -> None:
        return None


class LoopTrackingManager:
    def __init__(self) -> None:
        self._username = "alice"
        self._tokenid = "token"
        self._expires = 7200.0
        self.loop_ids: list[int] = []

    async def initialize(self) -> None:
        self.loop_ids.append(id(asyncio.get_running_loop()))

    async def get_entrydoc(self):
        self.loop_ids.append(id(asyncio.get_running_loop()))
        return [{"name": "home"}]

    async def close(self) -> None:
        self.loop_ids.append(id(asyncio.get_running_loop()))


class DummyState:
    runtime_session: RuntimeSession | None = None


def _runtime() -> RuntimeContext:
    return RuntimeContext(
        profile_name="work",
        session_mode=SessionMode.EPHEMERAL,
        shared_environment=False,
        profile_config=ProfileConfig(host="example.test"),
        credential_store=MemoryCredentialStore(),
    )


def test_session_state_contains_only_plain_persistent_data() -> None:
    assert [item.name for item in fields(SessionState)] == [
        "mode",
        "profile_name",
        "host",
        "username",
        "token",
        "expires_at",
        "home_path",
        "created_at",
        "pid",
    ]

    annotations = SessionState.__annotations__
    rendered = " ".join(str(value) for value in annotations.values())
    for forbidden in ("Manager", "Client", "Lock", "Event", "Future", "Task"):
        assert forbidden not in rendered


def test_runtime_session_manager_lifecycle_stays_on_creator_loop(monkeypatch) -> None:
    monkeypatch.setattr("pansh.session.getpass.getpass", lambda prompt: "password")
    monkeypatch.setattr("pansh.session.rsa_encrypt", lambda password, pubkey: "cipher")
    manager = LoopTrackingManager()
    controller = SessionController(_runtime(), manager_factory=lambda *args, **kwargs: manager)
    state = DummyState()

    async def scenario() -> None:
        runtime_session = await controller.require_session(
            state=state,
            console=DummyConsole(),
        )
        assert runtime_session.manager is manager
        assert runtime_session.state.profile_name == "work"
        await controller.refresh_session(state=state)
        await controller.close(state=state)

    asyncio.run(scenario())

    assert len(manager.loop_ids) == 4
    assert len(set(manager.loop_ids)) == 1
    assert controller.runtime_session is None
    assert state.runtime_session is None
