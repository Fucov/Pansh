from __future__ import annotations

import pytest

from pansh import auth, network
from pansh.api import AsyncApiManager


def test_api_manager_applies_profile_tls_setting(monkeypatch) -> None:
    captured: list[bool] = []
    client = object()

    def fake_client(*, verify_tls: bool):
        captured.append(verify_tls)
        return client

    monkeypatch.setattr(network, "create_async_client", fake_client)
    manager = AsyncApiManager(
        "example.test", "user", None, "public-key", verify_tls=False
    )

    assert manager.client is client
    assert captured == [False]


def test_token_login_applies_profile_tls_setting(monkeypatch) -> None:
    captured: list[bool] = []

    def fail_client(*, follow_redirects: bool, verify_tls: bool):
        captured.append(verify_tls)
        raise RuntimeError("stop after client creation")

    monkeypatch.setattr(network, "create_client", fail_client)

    with pytest.raises(RuntimeError, match="stop after client creation"):
        auth.get_access_token("https://example.test", "user", "cipher", verify_tls=False)

    assert captured == [False]
