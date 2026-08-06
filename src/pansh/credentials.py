"""Credential stores for persistent and process-local authentication."""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Protocol

from filelock import FileLock
from pydantic import ValidationError

from .models import AuthRecord


class CredentialStore(Protocol):
    def load(self) -> AuthRecord: ...

    def save(self, record: AuthRecord) -> None: ...

    def clear(self) -> None: ...

    def describe(self) -> str: ...


class MemoryCredentialStore:
    def __init__(self) -> None:
        self._record = AuthRecord()

    def load(self) -> AuthRecord:
        return self._record.model_copy(deep=True)

    def save(self, record: AuthRecord) -> None:
        self._record = record.model_copy(deep=True)

    def clear(self) -> None:
        self._record = AuthRecord()

    def describe(self) -> str:
        return "memory"


class FileCredentialStore:
    def __init__(self, path: Path) -> None:
        self.path = path
        self._lock = FileLock(str(path) + ".lock")

    def load(self) -> AuthRecord:
        with self._lock:
            if not self.path.exists():
                return AuthRecord()
            try:
                return AuthRecord.model_validate_json(self.path.read_text(encoding="utf-8"))
            except (OSError, UnicodeError, ValidationError, ValueError) as exc:
                raise RuntimeError(f"无法读取认证文件：{self.path}") from exc

    def save(self, record: AuthRecord) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        _best_effort_chmod(self.path.parent, 0o700)
        payload = json.dumps(record.model_dump(mode="json"), ensure_ascii=False, indent=2)
        with self._lock:
            temporary: Path | None = None
            try:
                with tempfile.NamedTemporaryFile(
                    "w",
                    encoding="utf-8",
                    dir=self.path.parent,
                    prefix=f".{self.path.name}.",
                    suffix=".tmp",
                    delete=False,
                ) as handle:
                    temporary = Path(handle.name)
                    handle.write(payload)
                    handle.flush()
                    try:
                        os.fsync(handle.fileno())
                    except OSError:
                        pass
                _best_effort_chmod(temporary, 0o600)
                os.replace(temporary, self.path)
                temporary = None
                _best_effort_chmod(self.path, 0o600)
                _best_effort_fsync_directory(self.path.parent)
            finally:
                if temporary is not None:
                    temporary.unlink(missing_ok=True)

    def clear(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._lock:
            self.path.unlink(missing_ok=True)

    def describe(self) -> str:
        return str(self.path)


def _best_effort_chmod(path: Path, mode: int) -> None:
    if os.name != "posix":
        return
    try:
        path.chmod(mode)
    except OSError:
        pass


def _best_effort_fsync_directory(path: Path) -> None:
    if os.name != "posix":
        return
    try:
        descriptor = os.open(path, os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(descriptor)
    except OSError:
        pass
    finally:
        os.close(descriptor)
