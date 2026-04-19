from __future__ import annotations

from pansh.main import (
    AppState,
    cli_callback,
    _parse_download_targets,
    _parse_upload_targets,
    _should_persist_login,
)
from pansh.session import SessionController
from pansh.theme import UIOptions


def _dummy_state(*, no_store_login: bool = False) -> AppState:
    return AppState(
        ui=UIOptions(),
        console=None,
        stderr_console=None,
        settings=None,
        once=no_store_login,
        session_controller=SessionController(),
    )


def test_session_only_login_policy() -> None:
    assert _should_persist_login(_dummy_state(), False) is True
    assert _should_persist_login(_dummy_state(no_store_login=True), False) is False
    assert _should_persist_login(_dummy_state(), True) is False


def test_parse_upload_targets_defaults_to_current_remote_dir() -> None:
    sources, remote = _parse_upload_targets(["README.md", "pyproject.toml"], False)
    assert sources == ["README.md", "pyproject.toml"]
    assert remote == "."


def test_parse_download_targets_defaults_to_current_local_dir() -> None:
    roots, dest = _parse_download_targets(["one.docx", "two.docx"], False)
    assert roots == ["one.docx", "two.docx"]
    assert dest == "."


def test_cli_callback_preserves_existing_state() -> None:
    state = _dummy_state(no_store_login=True)

    class DummyContext:
        def __init__(self, obj):
            self.obj = obj

    ctx = DummyContext(state)
    cli_callback(ctx, no_store_login=False)
    assert ctx.obj is state
    assert state.once is True
