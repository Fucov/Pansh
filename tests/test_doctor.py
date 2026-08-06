from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from pansh.main import app
from pansh.version import __version__


runner = CliRunner()


def test_doctor_reports_safe_runtime_and_package_details(tmp_path: Path) -> None:
    result = runner.invoke(
        app,
        ["--profile", "work", "--ephemeral", "doctor", "--json"],
        env={
            "PANSH_CONFIG": str(tmp_path / "config" / "settings.yaml"),
            "PANSH_AUTH_DIR": str(tmp_path / "state"),
        },
    )

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["pansh_version"] == __version__
    assert payload["build_commit"]
    assert payload["package_source"].endswith("pansh")
    assert payload["python_version"]
    assert payload["httpx_version"]
    assert payload["anyio_version"]
    assert payload["session_mode"] == "ephemeral"
    assert payload["profile_name"] == "work"
    assert payload["auth_store_type"] == "MemoryCredentialStore"
    assert payload["auth_store_path"] == "memory"

    serialized = json.dumps(payload).lower()
    for secret in ("token", "password", "encrypted"):
        assert secret not in serialized


def test_build_backend_accepts_only_commit_like_metadata(monkeypatch) -> None:
    import build_backend

    monkeypatch.setenv("PANSH_BUILD_COMMIT", "a" * 40)
    assert build_backend.resolve_build_commit() == "a" * 40

    monkeypatch.setenv("PANSH_BUILD_COMMIT", "token=secret")
    assert build_backend.resolve_build_commit() == "unknown"
    assert callable(build_backend.build_editable)
    assert callable(build_backend.get_requires_for_build_editable)


def test_build_backend_restores_source_metadata_after_wheel_build(
    monkeypatch,
    tmp_path: Path,
) -> None:
    import build_backend

    build_info = tmp_path / "_build_commit.py"
    build_info.write_text('BUILD_COMMIT = "unknown"\n', encoding="utf-8")
    monkeypatch.setattr(build_backend, "BUILD_INFO_FILE", build_info)
    monkeypatch.setenv("PANSH_BUILD_COMMIT", "b" * 40)

    def fake_builder(*args, **kwargs):
        assert f'BUILD_COMMIT = "{"b" * 40}"' in build_info.read_text()
        return "pansh.whl"

    result = build_backend._with_embedded_commit(fake_builder)

    assert result == "pansh.whl"
    assert build_info.read_text(encoding="utf-8") == 'BUILD_COMMIT = "unknown"\n'
