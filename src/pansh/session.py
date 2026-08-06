"""Session lifecycle management for pansh."""

from __future__ import annotations

import getpass
import os
import time
from dataclasses import dataclass, field
from typing import Any, Callable

from .api import AsyncApiManager, WrongPasswordException
from .auth import rsa_encrypt
from .models import AuthRecord, CachedToken, SessionMode
from .runtime import RuntimeContext


class SessionLoginError(Exception):
    """Raised when a login flow cannot establish a usable session."""


@dataclass
class Session:
    mode: SessionMode
    host: str
    username: str
    token: str = field(repr=False)
    expires_at: float
    home_path: str
    manager: AsyncApiManager
    created_at: float
    pid: int


class SessionController:
    def __init__(
        self,
        runtime: RuntimeContext,
        *,
        clock: Callable[[], float] | None = None,
        pid_getter: Callable[[], int] | None = None,
        manager_factory: Callable[..., AsyncApiManager] | None = None,
    ) -> None:
        self.runtime = runtime
        self.clock = clock or time.time
        self.pid_getter = pid_getter or os.getpid
        self.manager_factory = manager_factory or AsyncApiManager
        self.session: Session | None = None

    @property
    def store(self):
        return self.runtime.credential_store

    def _record_from_manager(
        self,
        *,
        username: str,
        encrypted: str | None,
        manager: AsyncApiManager,
    ) -> AuthRecord:
        return AuthRecord(
            username=username,
            encrypted=encrypted if self.runtime.profile_config.store_password else None,
            cached_token=CachedToken(token=manager._tokenid, expires=manager._expires),
        )

    async def create_session(
        self,
        *,
        state: Any,
        console: Any,
        force_reauth: bool = False,
    ) -> Session:
        profile = self.runtime.profile_config
        record = self.store.load()
        username = record.username or console.input("Username: ")
        encrypted = record.encrypted
        password: str | None = None
        cached_token = record.cached_token.model_copy()

        if force_reauth:
            cached_token = CachedToken()

        has_cached_token = bool(cached_token.token) and self.clock() < cached_token.expires
        if (force_reauth or not has_cached_token) and (
            not encrypted or not profile.store_password
        ):
            password = getpass.getpass("Password: ")
            encrypted = rsa_encrypt(password, profile.pubkey)

        for attempt in range(3):
            manager = self.manager_factory(
                profile.host,
                username,
                password,
                profile.pubkey,
                encrypted=encrypted,
                cached_token=cached_token.token or None,
                cached_expire=cached_token.expires or None,
                verify_tls=profile.verify_tls,
            )
            try:
                with console.status("Connecting..."):
                    await manager.initialize()
                entrydoc = await manager.get_entrydoc()
                if not entrydoc:
                    await manager.close()
                    raise SessionLoginError("无法读取入口文档库。")
                updated = self._record_from_manager(
                    username=username,
                    encrypted=encrypted,
                    manager=manager,
                )
                self.store.save(updated)
                session = Session(
                    mode=self.runtime.session_mode,
                    host=profile.host,
                    username=username,
                    token=manager._tokenid,
                    expires_at=manager._expires,
                    home_path="/" + entrydoc[0]["name"],
                    manager=manager,
                    created_at=self.clock(),
                    pid=self.pid_getter(),
                )
                self.session = session
                state.session = session
                return session
            except WrongPasswordException:
                await manager.close()
                if attempt == 2:
                    break
                console.print("密码错误，请重试。", style="warning")
                password = getpass.getpass("Password: ")
                encrypted = rsa_encrypt(password, profile.pubkey)
                cached_token = CachedToken()
        raise SessionLoginError("认证失败。")

    async def refresh_session(self, *, state: Any) -> Session:
        if self.session is None:
            raise SessionLoginError("当前没有可复用的登录会话。")
        await self.session.manager.initialize()
        self.session.token = self.session.manager._tokenid
        self.session.expires_at = self.session.manager._expires
        record = self.store.load()
        self.store.save(
            self._record_from_manager(
                username=self.session.username,
                encrypted=record.encrypted,
                manager=self.session.manager,
            )
        )
        state.session = self.session
        return self.session

    async def require_session(
        self,
        *,
        state: Any,
        console: Any,
        force_reauth: bool = False,
    ) -> Session:
        if force_reauth and self.session is not None:
            await self.close(state=state)
        if self.session is not None and not force_reauth:
            return await self.refresh_session(state=state)
        return await self.create_session(
            state=state,
            console=console,
            force_reauth=force_reauth,
        )

    async def close(self, *, state: Any | None = None) -> None:
        if self.session is not None:
            await self.session.manager.close()
        self.session = None
        if state is not None:
            state.session = None

    async def logout(self, *, state: Any) -> None:
        self.store.clear()
        await self.close(state=state)
