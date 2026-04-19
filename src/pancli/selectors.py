"""Local and remote file selectors used by upload/download commands."""

from __future__ import annotations

import fnmatch
import re
from pathlib import Path
from typing import Iterable, Sequence

from .models import MatchField, SelectedLocalItem, SelectedRemoteItem


def _compile_regex(pattern: str | None) -> re.Pattern[str] | None:
    if not pattern:
        return None
    return re.compile(pattern)


def _matches(
    candidate: str,
    *,
    globs: Sequence[str],
    regex: re.Pattern[str] | None,
    excludes: Sequence[str],
) -> bool:
    include = True
    if globs:
        include = any(fnmatch.fnmatch(candidate, pattern) for pattern in globs)
    if include and regex is not None:
        include = bool(regex.search(candidate))
    if include and excludes:
        include = not any(fnmatch.fnmatch(candidate, pattern) for pattern in excludes)
    return include


def _iter_files(root: Path, recursive: bool) -> Iterable[Path]:
    if root.is_file():
        yield root
        return
    if not root.exists():
        return
    iterator = root.rglob("*") if recursive else root.glob("*")
    for path in iterator:
        if path.is_file():
            yield path


def select_local_files(
    roots: Sequence[str],
    *,
    globs: Sequence[str] = (),
    regex: str | None = None,
    excludes: Sequence[str] = (),
    recursive: bool = False,
    match_field: MatchField = MatchField.BASENAME,
) -> list[SelectedLocalItem]:
    compiled = _compile_regex(regex)
    matches: list[SelectedLocalItem] = []
    for root_text in roots:
        root = Path(root_text).expanduser().resolve()
        base_dir = root.parent if root.is_file() else root
        explicit_current_dir = root_text in {".", "./"}
        prefix = "" if explicit_current_dir else (root.name if root.is_dir() else "")
        for file_path in _iter_files(root, recursive):
            inner = str(file_path.relative_to(base_dir)) if base_dir.exists() else file_path.name
            rel = f"{prefix}/{inner}".strip("/")
            candidate = file_path.name if match_field == MatchField.BASENAME else rel
            if _matches(candidate, globs=globs, regex=compiled, excludes=excludes):
                matches.append(
                    SelectedLocalItem(
                        source_path=str(file_path),
                        relative_path=rel.replace("\\", "/"),
                        basename=file_path.name,
                        size=file_path.stat().st_size,
                    )
                )
    unique: dict[str, SelectedLocalItem] = {item.source_path: item for item in matches}
    return list(unique.values())


def filter_remote_items(
    items: Sequence[SelectedRemoteItem],
    *,
    globs: Sequence[str] = (),
    regex: str | None = None,
    excludes: Sequence[str] = (),
    match_field: MatchField = MatchField.BASENAME,
) -> list[SelectedRemoteItem]:
    compiled = _compile_regex(regex)
    results: list[SelectedRemoteItem] = []
    for item in items:
        candidate = item.basename if match_field == MatchField.BASENAME else item.relative_path
        if _matches(candidate, globs=globs, regex=compiled, excludes=excludes):
            results.append(item)
    return results
