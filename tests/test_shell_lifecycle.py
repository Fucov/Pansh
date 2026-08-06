from __future__ import annotations

import asyncio

from typer.main import get_command

from pansh.main import AppState, app
from pansh.credentials import MemoryCredentialStore
from pansh.models import ProfileConfig, ResourceInfo, SessionMode
from pansh.runtime import RuntimeContext
from pansh.session import RuntimeSession, SessionState
from pansh.shell import PanShell, run_interactive_shell
from pansh.theme import UIOptions


class DummyConsole:
    def print(self, *args, **kwargs) -> None:
        return None


class FakeManager:
    def __init__(self) -> None:
        self.loop_ids: list[int] = []

    async def get_resource_info_by_path(self, path: str):
        self.loop_ids.append(id(asyncio.get_running_loop()))
        return ResourceInfo(size=-1, docid=path or "home", name=path or "home")

    async def list_dir(self, docid: str, *, by: str | None = None):
        self.loop_ids.append(id(asyncio.get_running_loop()))
        return [], []


def _shell() -> PanShell:
    context = RuntimeContext(
        profile_name="work",
        session_mode=SessionMode.EPHEMERAL,
        shared_environment=False,
        profile_config=ProfileConfig(host="example.test"),
        credential_store=MemoryCredentialStore(),
    )
    console = DummyConsole()
    state = AppState(
        ui=UIOptions(),
        console=console,
        stderr_console=console,
        settings=type("Settings", (), {"default_jobs": 1, "search_depth": 3})(),
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
    state.runtime_session = RuntimeSession(
        state=session_state,
        manager=FakeManager(),
    )
    shell = PanShell(state)
    shell.manager = state.runtime_session.manager
    shell.home_root = "/home"
    shell.remote_cwd = "/home"
    return shell


def test_shell_network_commands_do_not_invoke_typer_command_main(monkeypatch) -> None:
    shell = _shell()
    root_command = get_command(app)
    calls = 0

    def fail_main(*args, **kwargs):
        nonlocal calls
        calls += 1
        raise AssertionError("shell network commands must not invoke Typer main")

    root_command.main = fail_main
    monkeypatch.setattr("pansh.shell.get_command", lambda application: root_command)

    asyncio.run(shell.handle("ls ."))

    assert calls == 0
    assert len(shell.manager.loop_ids) == 2


def test_shell_ls_cd_ls_download_sequence_uses_one_loop(monkeypatch) -> None:
    shell = _shell()
    download_loop_ids: list[int] = []

    async def fake_download(runtime, items=None, *, state, **kwargs):
        download_loop_ids.append(id(asyncio.get_running_loop()))

    monkeypatch.setattr("pansh.main.execute_download", fake_download)

    async def scenario() -> None:
        for command in (
            "ls .",
            "cd child",
            "ls .",
            "download file.txt . --yes",
            "cd ..",
            "ls .",
        ):
            assert await shell.handle(command) is False
        for _ in range(17):
            await shell.handle("ls .")
        await shell.handle("download one.txt . --yes")

    asyncio.run(scenario())

    all_loop_ids = shell.manager.loop_ids + download_loop_ids
    assert len(download_loop_ids) == 2
    assert len(set(all_loop_ids)) == 1


def test_interactive_shell_has_one_top_level_asyncio_run(monkeypatch) -> None:
    shell = _shell()
    real_run = asyncio.run
    run_calls = 0

    async def fake_shell_run(state, *, login=True):
        await shell.handle("ls .")

    def counting_run(coro):
        nonlocal run_calls
        run_calls += 1
        return real_run(coro)

    monkeypatch.setattr("pansh.shell.run_shell_with_state", fake_shell_run)
    monkeypatch.setattr("pansh.shell.asyncio.run", counting_run)

    run_interactive_shell(shell.state)

    assert run_calls == 1
    assert len(set(shell.manager.loop_ids)) == 1
