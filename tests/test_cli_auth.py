from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from pansh.main import AppState, _whoami_payload, app
from pansh.runtime import resolve_runtime_context
from pansh.session import SessionController
from pansh.settings import Settings
from pansh.theme import UIOptions


runner = CliRunner()


def _isolated_env(tmp_path: Path) -> dict[str, str]:
    return {
        "PANSH_CONFIG": str(tmp_path / "config" / "settings.yaml"),
        "PANSH_AUTH_DIR": str(tmp_path / "state"),
    }


def test_global_ephemeral_and_profile_options_resolve_runtime(tmp_path: Path) -> None:
    result = runner.invoke(
        app,
        ["--profile", "work1", "--ephemeral", "profiles", "path", "work1"],
        env=_isolated_env(tmp_path),
    )

    assert result.exit_code == 0
    assert "work1" in result.stdout


def test_shell_subcommand_accepts_profile_and_ephemeral_options(monkeypatch, tmp_path: Path) -> None:
    captured: list[tuple[str, str]] = []

    def fake_shell(state) -> None:
        captured.append(
            (state.runtime_context.profile_name, state.runtime_context.session_mode.value)
        )

    monkeypatch.setattr("pansh.shell.run_interactive_shell", fake_shell)

    result = runner.invoke(
        app,
        ["shell", "--profile", "work1", "--ephemeral"],
        env=_isolated_env(tmp_path),
    )

    assert result.exit_code == 0
    assert captured == [("work1", "ephemeral")]


def test_profiles_create_list_path_and_delete(tmp_path: Path) -> None:
    env = _isolated_env(tmp_path)

    created = runner.invoke(app, ["profiles", "create", "alpha"], env=env)
    listed = runner.invoke(app, ["profiles", "list"], env=env)
    path = runner.invoke(app, ["profiles", "path", "alpha"], env=env)
    deleted = runner.invoke(app, ["profiles", "delete", "alpha"], env=env)
    listed_after = runner.invoke(app, ["profiles", "list"], env=env)

    assert created.exit_code == listed.exit_code == path.exit_code == deleted.exit_code == 0
    assert "alpha" in listed.stdout
    assert "profile.yaml" in path.stdout
    assert "auth.json" in path.stdout
    assert "alpha" not in listed_after.stdout


def test_profiles_reject_traversal(tmp_path: Path) -> None:
    result = runner.invoke(
        app, ["profiles", "create", "../escape"], env=_isolated_env(tmp_path)
    )

    assert result.exit_code != 0
    assert "profile" in result.output.lower()


def test_login_no_store_non_tty_exits_before_authentication(monkeypatch, tmp_path: Path) -> None:
    called = False

    async def fail_login(*args, **kwargs):
        nonlocal called
        called = True
        raise AssertionError("login must not run")

    monkeypatch.setattr("pansh.main._is_interactive_tty", lambda: False)
    monkeypatch.setattr("pansh.main._login", fail_login)

    result = runner.invoke(
        app, ["login", "--no-store"], env=_isolated_env(tmp_path)
    )

    assert result.exit_code != 0
    assert "pansh --ephemeral <command>" in result.output
    assert called is False


def test_login_no_store_tty_enters_same_process_shell(monkeypatch, tmp_path: Path) -> None:
    events: list[str] = []

    async def fake_login(console, *, state, force_reauth=False):
        events.append(f"login:{state.runtime_context.session_mode.value}:{force_reauth}")
        return object(), "/home"

    async def fake_shell(state, *, login=True):
        events.append(f"shell:{login}")

    monkeypatch.setattr("pansh.main._is_interactive_tty", lambda: True)
    monkeypatch.setattr("pansh.main._login", fake_login)
    monkeypatch.setattr("pansh.shell.run_shell_with_state", fake_shell)

    result = runner.invoke(
        app, ["login", "--no-store"], env=_isolated_env(tmp_path)
    )

    assert result.exit_code == 0
    assert "临时登录无法跨进程保存，正在进入 ephemeral shell。" in result.output
    assert events == ["login:ephemeral:True", "shell:False"]


def test_ephemeral_single_command_uses_ephemeral_runtime(monkeypatch, tmp_path: Path) -> None:
    modes: list[str] = []

    class Directory:
        is_dir = True
        docid = "root"

    class Manager:
        async def get_resource_info_by_path(self, path):
            return Directory()

        async def list_dir(self, docid, by="name"):
            return [], []

    async def fake_with_manager(ctx):
        modes.append(ctx.obj.runtime_context.session_mode.value)
        return Manager(), "/home"

    async def fake_release(ctx, manager=None):
        return None

    monkeypatch.setattr("pansh.main._with_manager", fake_with_manager)
    monkeypatch.setattr("pansh.main._release_manager", fake_release)

    result = runner.invoke(
        app, ["--ephemeral", "ls", "."], env=_isolated_env(tmp_path)
    )

    assert result.exit_code == 0
    assert modes == ["ephemeral"]


def test_whoami_payload_contains_context_without_secrets(tmp_path: Path) -> None:
    settings_path = tmp_path / "settings.yaml"
    settings_path.write_text("auth:\n  default_profile: default\n", encoding="utf-8")
    settings = Settings(settings_path)
    runtime = resolve_runtime_context(
        settings,
        profile_name="work1",
        ephemeral=True,
        config_dir=tmp_path / "config",
        auth_dir=tmp_path / "state",
    )
    state = AppState(
        ui=UIOptions(),
        console=None,
        stderr_console=None,
        settings=settings,
        runtime_context=runtime,
        session_controller=SessionController(runtime),
    )

    payload = _whoami_payload(state, username="alice", home="/home")
    serialized = json.dumps(payload)

    assert payload["session_mode"] == "ephemeral"
    assert payload["profile"] == "work1"
    assert payload["auth_store"] == "memory"
    assert "token" not in serialized
    assert "encrypted" not in serialized
