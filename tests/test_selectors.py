from __future__ import annotations

from pansh.models import MatchField
from pansh.selectors import select_local_files


def test_select_local_files_with_glob() -> None:
    matches = select_local_files(["."], globs=["README.md", "LICENSE"], recursive=False)
    assert {item.basename for item in matches} == {"README.md", "LICENSE"}


def test_select_local_files_with_regex_and_relpath() -> None:
    matches = select_local_files(
        ["src/pansh"],
        regex=r".*settings\.yaml$",
        recursive=True,
        match_field=MatchField.RELPATH,
    )
    assert {item.relative_path for item in matches} == {"pansh/defaults/settings.yaml"}
