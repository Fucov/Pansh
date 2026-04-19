"""Pydantic data models used by PanCLI."""

from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class ThemeMode(str, Enum):
    AUTO = "auto"
    DARK = "dark"
    LIGHT = "light"
    PLAIN = "plain"


class TransferStatus(str, Enum):
    QUEUED = "queued"
    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"
    SKIPPED = "skipped"
    CANCELLED = "cancelled"


class MatchField(str, Enum):
    BASENAME = "basename"
    RELPATH = "relpath"


class CachedToken(BaseModel):
    token: str = ""
    expires: float = 0.0


DEFAULT_PUBKEY = """-----BEGIN PUBLIC KEY-----
MIIBIjANBgkqhkiG9w0BAQEFAAOCAQ8AMIIBCgKCAQEA4E+eiWRwffhRIPQYvlXU
jf0b3HqCmosiCxbFCYI/gdfDBhrTUzbt3fL3o/gRQQBEPf69vhJMFH2ZMtaJM6oh
E3yQef331liPVM0YvqMOgvoID+zDa1NIZFObSsjOKhvZtv9esO0REeiVEPKNc+Dp
6il3x7TV9VKGEv0+iriNjqv7TGAexo2jVtLm50iVKTju2qmCDG83SnVHzsiNj70M
iviqiLpgz72IxjF+xN4bRw8I5dD0GwwO8kDoJUGWgTds+VckCwdtZA65oui9Osk5
t1a4pg6Xu9+HFcEuqwJTDxATvGAz1/YW0oUisjM0ObKTRDVSfnTYeaBsN6L+M+8g
CwIDAQAB
-----END PUBLIC KEY-----"""


class AppConfig(BaseModel):
    revision: int = 5
    host: str = "bhpan.buaa.edu.cn"
    pubkey: str = DEFAULT_PUBKEY
    username: str | None = None
    encrypted: str | None = None
    store_password: bool = True
    verify_tls: bool = True
    cached_token: CachedToken = Field(default_factory=CachedToken)
    theme: ThemeMode = ThemeMode.AUTO


class ResourceInfo(BaseModel):
    size: int = 0
    docid: str = ""
    name: str = ""
    rev: str = ""
    client_mtime: int = 0
    modified: int = 0

    @property
    def is_dir(self) -> bool:
        return self.size == -1


class DirEntry(BaseModel):
    docid: str
    name: str
    size: int
    modified: int
    creator: str | None = None
    is_dir: bool = False

    @classmethod
    def from_dict(cls, data: dict[str, Any], is_dir: bool = False) -> "DirEntry":
        return cls(
            docid=data.get("docid", ""),
            name=data.get("name", ""),
            size=data.get("size", -1 if is_dir else 0),
            modified=data.get("modified", 0),
            creator=data.get("creator"),
            is_dir=is_dir,
        )


class FileMetaData(BaseModel):
    size: int = 0
    docid: str = ""
    rev: str = ""
    modified: int = 0
    client_mtime: int = 0
    name: str = ""
    editor: str = ""
    site: str = ""
    tags: list[str] = Field(default_factory=list)


class LinkInfo(BaseModel):
    link: str = ""
    password: str = ""
    perm: int = 0
    endtime: int = 0
    limittimes: int = -1


class SearchResult(BaseModel):
    path: str
    name: str
    size: int
    modified: int
    is_dir: bool


class TransferTask(BaseModel):
    remote_path: str = ""
    local_path: str = ""
    size: int = 0
    transferred: int = 0
    status: TransferStatus = TransferStatus.QUEUED
    error: str | None = None
    speed: float = 0.0
    average_speed: float = 0.0
    docid: str | None = None


class SelectedLocalItem(BaseModel):
    source_path: str
    relative_path: str
    basename: str
    size: int


class SelectedRemoteItem(BaseModel):
    remote_path: str
    relative_path: str
    basename: str
    size: int
    docid: str


class RevisionInfo(BaseModel):
    rev: str
    name: str = ""
    size: int = 0
    modified: int = 0
    client_mtime: int = 0
    editor: str = ""


class QuotaInfo(BaseModel):
    quota_used: int = 0
    quota_allocated: int = 0
    space_rate: str = ""
