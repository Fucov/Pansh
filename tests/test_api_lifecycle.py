from __future__ import annotations

import asyncio
import time

import pytest

from pansh.api import AsyncApiManager


class FakeClient:
    def __init__(self, loop_ids: list[int]) -> None:
        self.loop_ids = loop_ids
        self.close_calls = 0

    async def aclose(self) -> None:
        self.loop_ids.append(id(asyncio.get_running_loop()))
        self.close_calls += 1


def _manager() -> AsyncApiManager:
    return AsyncApiManager(
        "example.test",
        "alice",
        None,
        "pubkey",
        cached_token="token",
        cached_expire=time.time() + 3600,
    )


def test_manager_initialize_request_and_close_use_one_loop(monkeypatch) -> None:
    loop_ids: list[int] = []
    client = FakeClient(loop_ids)

    async def fake_get(url, *, tokenid=None, client=None):
        loop_ids.append(id(asyncio.get_running_loop()))
        return []

    monkeypatch.setattr("pansh.network.create_async_client", lambda **kwargs: client)
    monkeypatch.setattr("pansh.network.async_get_json", fake_get)
    manager = _manager()

    async def scenario() -> None:
        await manager.initialize()
        await manager.get_entrydoc()
        await manager.close()

    asyncio.run(scenario())

    assert len(loop_ids) == 3
    assert len(set(loop_ids)) == 1
    assert client.close_calls == 1


def test_manager_rejects_request_from_a_different_loop(monkeypatch) -> None:
    async def fake_get(url, *, tokenid=None, client=None):
        return []

    monkeypatch.setattr("pansh.network.create_async_client", lambda **kwargs: FakeClient([]))
    monkeypatch.setattr("pansh.network.async_get_json", fake_get)
    manager = _manager()

    asyncio.run(manager.get_entrydoc())

    with pytest.raises(
        RuntimeError,
        match="AsyncApiManager cannot be reused across event loops",
    ):
        asyncio.run(manager.get_entrydoc())


def test_manager_rejects_wrong_loop_close_before_touching_client(monkeypatch) -> None:
    client = FakeClient([])

    async def fake_get(url, *, tokenid=None, client=None):
        return []

    monkeypatch.setattr("pansh.network.create_async_client", lambda **kwargs: client)
    monkeypatch.setattr("pansh.network.async_get_json", fake_get)
    manager = _manager()

    asyncio.run(manager.get_entrydoc())

    with pytest.raises(
        RuntimeError,
        match="AsyncApiManager cannot be reused across event loops",
    ):
        asyncio.run(manager.close())

    assert client.close_calls == 0
