from __future__ import annotations

from pathlib import Path

from pansh.settings import Settings, get_settings_path


def test_settings_path_honors_env_override(monkeypatch) -> None:
    settings_path = Path.cwd() / "settings.yaml"
    monkeypatch.setenv("PANSH_CONFIG", str(settings_path))
    assert get_settings_path() == settings_path


def test_settings_reads_existing_file() -> None:
    settings = Settings(Path("settings.yaml"))
    assert settings.theme_mode == "auto"
    assert settings.default_jobs == 4
