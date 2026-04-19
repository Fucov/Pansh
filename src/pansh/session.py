"""Session lifecycle management for pansh."""

from __future__ import annotations

import getpass
import os
import time
from dataclasses import dataclass
from typing import Any, Callable

from .api import AsyncApiManager, WrongPasswordException
from .auth import rsa_encrypt
from .config import load_config, save_config
from .models import AppConfig


class SessionLoginError(Exception):
    """Raised when a login flow cannot establish a usable session."""


@dataclass
class Session:
    mode: str
    host: str
    username: str
    token: str
    expires_at: float
    home_path: str
    manager: AsyncApiManager
    created_at: float
    pid: int


class SessionController:
    def __init__(
        self,
        *,
        clock: Callable[[], float] | None = None,
        pid_getter: Callable[[], int] | None = None,
    ) -> None:
        self.clock = clock or time.time
        self.pid_getter = pid_getter or os.getpid
        self.session: Session | None = None

    def _persist_session(self, cfg: AppConfig, *, username: str, manager: AsyncApiManager) -> None:
        cfg.username = username
        cfg.cached_token.token = manager._tokenid
        cfg.cached_token.expires = manager._expires
        save_config(cfg)

    def make_manager(self, *, state: Any) -> AsyncApiManager:
        cfg = state.session_config or load_config()
        state.session_config = cfg
        username = ""
        token = cfg.cached_token.token or None
        expires = cfg.cached_token.expires or None
        if state.session is not None:
            username = state.session.username
            token = state.session.token or token
            expires = state.session.expires_at or expires
        elif cfg.username:
            username = cfg.username
        if not username:
            raise SessionLoginError("当前没有可复用的登录会话。")
        return AsyncApiManager(
            cfg.host,
            username,
            None,
            cfg.pubkey,
            encrypted=cfg.encrypted,
            cached_token=token,
            cached_expire=expires,
        )

    def sync_manager_state(self, *, state: Any, manager: AsyncApiManager) -> None:
        cfg = state.session_config or load_config()
        state.session_config = cfg
        cfg.username = manager._username
        cfg.cached_token.token = manager._tokenid
        cfg.cached_token.expires = manager._expires
        if self.session is not None:
            self.session.token = manager._tokenid
            self.session.expires_at = manager._expires
            state.session = self.session
        if self.session is not None and self.session.mode == "persistent":
            self._persist_session(cfg, username=manager._username, manager=manager)

    async def create_session(
        self,
        *,
        state: Any,
        console: Any,
        no_store: bool = False,
        force_reauth: bool = False,
    ) -> Session:
        cfg = state.session_config or load_config()
        state.session_config = cfg
        persistent = not state.once and not no_store
        username = cfg.username or console.input("Username: ")
        encrypted = cfg.encrypted
        password: str | None = None

        if force_reauth:
            cfg.cached_token.token = ""
            cfg.cached_token.expires = 0

        has_cached_token = bool(cfg.cached_token.token) and self.clock() < (cfg.cached_token.expires or 0)
        if (force_reauth or not has_cached_token) and (not encrypted or not cfg.store_password):
            password = getpass.getpass("Password: ")
            encrypted = rsa_encrypt(password, cfg.pubkey)
            if cfg.store_password and persistent:
                cfg.encrypted = encrypted

        for attempt in range(3):
            manager = AsyncApiManager(
                cfg.host,
                username,
                password,
                cfg.pubkey,
                encrypted=encrypted,
                cached_token=cfg.cached_token.token or None,
                cached_expire=cfg.cached_token.expires or None,
            )
            try:
                with console.status("Connecting..."):
                    await manager.initialize()
                entrydoc = await manager.get_entrydoc()
                if not entrydoc:
                    await manager.close()
                    raise SessionLoginError("无法读取入口文档库。")
                if persistent:
                    self._persist_session(cfg, username=username, manager=manager)
                else:
                    cfg.username = username
                    if encrypted:
                        cfg.encrypted = encrypted
                    cfg.cached_token.token = manager._tokenid
                    cfg.cached_token.expires = manager._expires
                session = Session(
                    mode="persistent" if persistent else "ephemeral",
                    host=cfg.host,
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
                encrypted = rsa_encrypt(password, cfg.pubkey)
                if cfg.store_password and persistent:
                    cfg.encrypted = encrypted
        raise SessionLoginError("认证失败。")

    async def refresh_session(self, *, state: Any) -> Session:
        if self.session is None:
            raise SessionLoginError("当前没有可复用的登录会话。")
        await self.session.manager.initialize()
        self.session.token = self.session.manager._tokenid
        self.session.expires_at = self.session.manager._expires
        if self.session.mode == "persistent":
            cfg = state.session_config or load_config()
            state.session_config = cfg
            self._persist_session(cfg, username=self.session.username, manager=self.session.manager)
        else:
            cfg = state.session_config or load_config()
            state.session_config = cfg
            cfg.username = self.session.username
            cfg.cached_token.token = self.session.token
            cfg.cached_token.expires = self.session.expires_at
        state.session = self.session
        return self.session

    async def require_session(
        self,
        *,
        state: Any,
        console: Any,
        no_store: bool = False,
        force_reauth: bool = False,
    ) -> Session:
        if force_reauth and self.session is not None:
            await self.close(state=state)
        if self.session is not None and not force_reauth:
            return await self.refresh_session(state=state)
        return await self.create_session(
            state=state,
            console=console,
            no_store=no_store,
            force_reauth=force_reauth,
        )

    async def close(self, *, state: Any | None = None) -> None:
        if self.session is not None:
            await self.session.manager.close()
        self.session = None
        if state is not None:
            state.session = None

    async def logout(self, *, state: Any) -> None:
        session = self.session
        if session is not None and session.mode == "persistent":
            cfg = load_config()
            cfg.username = None
            cfg.encrypted = None
            cfg.cached_token.token = ""
            cfg.cached_token.expires = 0
            save_config(cfg)
        await self.close(state=state)
