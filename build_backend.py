"""PEP 517 wrapper that embeds a source commit in built wheels."""

from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path
from typing import Any, Callable


ROOT = Path(__file__).resolve().parent
BUILD_INFO_FILE = ROOT / "src" / "pansh" / "_build_commit.py"
COMMIT_PATTERN = re.compile(r"^[0-9a-fA-F]{7,64}$")


def resolve_build_commit() -> str:
    configured = os.environ.get("PANSH_BUILD_COMMIT")
    if configured is not None:
        return configured.lower() if COMMIT_PATTERN.fullmatch(configured) else "unknown"
    try:
        commit = subprocess.check_output(
            ["git", "rev-parse", "HEAD"],
            cwd=ROOT,
            stderr=subprocess.DEVNULL,
            text=True,
        ).strip()
    except (OSError, subprocess.CalledProcessError):
        return "unknown"
    return commit.lower() if COMMIT_PATTERN.fullmatch(commit) else "unknown"


def _with_embedded_commit(builder: Callable[..., str], *args: Any, **kwargs: Any) -> str:
    original = BUILD_INFO_FILE.read_bytes()
    commit = resolve_build_commit()
    BUILD_INFO_FILE.write_text(
        '"""Build metadata generated while creating this distribution."""\n\n'
        f'BUILD_COMMIT = "{commit}"\n',
        encoding="utf-8",
    )
    try:
        return builder(*args, **kwargs)
    finally:
        BUILD_INFO_FILE.write_bytes(original)


def build_wheel(
    wheel_directory: str,
    config_settings: dict[str, Any] | None = None,
    metadata_directory: str | None = None,
) -> str:
    from hatchling.build import build_wheel as hatch_build_wheel

    return _with_embedded_commit(
        hatch_build_wheel,
        wheel_directory,
        config_settings,
        metadata_directory,
    )


def build_sdist(
    sdist_directory: str,
    config_settings: dict[str, Any] | None = None,
) -> str:
    from hatchling.build import build_sdist as hatch_build_sdist

    return hatch_build_sdist(sdist_directory, config_settings)


def build_editable(
    wheel_directory: str,
    config_settings: dict[str, Any] | None = None,
    metadata_directory: str | None = None,
) -> str:
    from hatchling.build import build_editable as hatch_build_editable

    return hatch_build_editable(
        wheel_directory,
        config_settings,
        metadata_directory,
    )


def get_requires_for_build_wheel(
    config_settings: dict[str, Any] | None = None,
) -> list[str]:
    from hatchling.build import get_requires_for_build_wheel as hatch_requires

    return hatch_requires(config_settings)


def get_requires_for_build_sdist(
    config_settings: dict[str, Any] | None = None,
) -> list[str]:
    from hatchling.build import get_requires_for_build_sdist as hatch_requires

    return hatch_requires(config_settings)


def get_requires_for_build_editable(
    config_settings: dict[str, Any] | None = None,
) -> list[str]:
    from hatchling.build import get_requires_for_build_editable as hatch_requires

    return hatch_requires(config_settings)


def prepare_metadata_for_build_wheel(
    metadata_directory: str,
    config_settings: dict[str, Any] | None = None,
) -> str:
    from hatchling.build import (
        prepare_metadata_for_build_wheel as hatch_prepare_metadata,
    )

    return hatch_prepare_metadata(metadata_directory, config_settings)
