from __future__ import annotations

import asyncio

from pansh.main import (
    AppState,
    execute_cat,
    execute_cp,
    execute_download,
    execute_find,
    execute_ls,
    execute_mkdir,
    execute_mv,
    execute_rm,
    execute_stat,
    execute_touch,
    execute_tree,
    execute_upload,
    execute_whoami,
)
from pansh.credentials import MemoryCredentialStore
from pansh.models import ProfileConfig, ResourceInfo, SessionMode
from pansh.runtime import RuntimeContext
from pansh.session import RuntimeSession, SessionState
from pansh.theme import UIOptions


class RecordingConsole:
    def __init__(self) -> None:
        self.values: list[object] = []

    def print(self, value, *args, **kwargs) -> None:
        self.values.append(value)


class FakeManager:
    def __init__(self) -> None:
        self.loop_ids: list[int] = []

    async def get_resource_info_by_path(self, path: str):
        self.loop_ids.append(id(asyncio.get_running_loop()))
        return ResourceInfo(size=-1, docid="home", name="home")

    async def list_dir(self, docid: str, *, by: str | None = None):
        self.loop_ids.append(id(asyncio.get_running_loop()))
        return [], []


def _state_and_runtime() -> tuple[AppState, RuntimeSession]:
    context = RuntimeContext(
        profile_name="work",
        session_mode=SessionMode.EPHEMERAL,
        shared_environment=False,
        profile_config=ProfileConfig(host="example.test"),
        credential_store=MemoryCredentialStore(),
    )
    console = RecordingConsole()
    state = AppState(
        ui=UIOptions(),
        console=console,
        stderr_console=console,
        settings=None,
        runtime_context=context,
    )
    session_state = SessionState(
        mode=SessionMode.EPHEMERAL,
        profile_name="work",
        host="example.test",
        username="alice",
        token="token",
        expires_at=7200,
        home_path="/home",
        created_at=1,
        pid=1,
    )
    return state, RuntimeSession(state=session_state, manager=FakeManager())


def test_execute_ls_reuses_runtime_for_twenty_commands_on_one_loop() -> None:
    state, runtime = _state_and_runtime()

    async def scenario() -> None:
        for _ in range(20):
            await execute_ls(runtime, ".", state=state)

    asyncio.run(scenario())

    assert len(runtime.manager.loop_ids) == 40
    assert len(set(runtime.manager.loop_ids)) == 1
    assert len(state.console.values) == 20


def test_all_network_commands_expose_awaitable_business_functions() -> None:
    for command in (
        execute_whoami,
        execute_ls,
        execute_tree,
        execute_stat,
        execute_find,
        execute_mkdir,
        execute_touch,
        execute_rm,
        execute_mv,
        execute_cp,
        execute_cat,
        execute_upload,
        execute_download,
    ):
        assert asyncio.iscoroutinefunction(command)
