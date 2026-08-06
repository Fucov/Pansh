from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pansh


def test_version_export() -> None:
    assert isinstance(pansh.__version__, str)
    assert pansh.__version__


def test_python_m_help() -> None:
    result = subprocess.run(
        [sys.executable, "-m", "pansh", "--help"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0
    assert "pansh" in result.stdout.lower()
    assert "quota" not in result.stdout
    assert "restore-revision" not in result.stdout
    assert "revisions" not in result.stdout
    assert "link" not in result.stdout


def test_once_alias_is_accepted() -> None:
    result = subprocess.run(
        [sys.executable, "-m", "pansh", "--once", "--version"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0
    assert pansh.__version__ in result.stdout


def test_core_command_help_smoke(tmp_path: Path) -> None:
    env = os.environ.copy()
    env.update(
        PANSH_CONFIG=str(tmp_path / "config" / "settings.yaml"),
        PANSH_AUTH_DIR=str(tmp_path / "state"),
    )
    for command in ("ls", "upload", "download", "login"):
        result = subprocess.run(
            [sys.executable, "-m", "pansh", command, "--help"],
            capture_output=True,
            text=True,
            check=False,
            env=env,
        )
        assert result.returncode == 0
        assert command in result.stdout.lower()


def test_pyproject_has_console_script() -> None:
    text = Path("pyproject.toml").read_text(encoding="utf-8")
    assert 'pansh = "pansh.main:main"' in text
