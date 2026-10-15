from __future__ import annotations

import json as _json

import pytest

from pansh import auth


class FakeCookies:
    def __init__(self) -> None:
        self.values: dict[str, str] = {}

    def get(self, name: str) -> str | None:
        return self.values.get(name)

    def set(self, name: str, value: str) -> None:
        self.values[name] = value


class FakeResponse:
    def __init__(
        self,
        *,
        text: str = "",
        status_code: int = 200,
        json_data: dict | None = None,
    ) -> None:
        self.text = text
        self.status_code = status_code
        self._json_data = json_data or {}

    def json(self) -> dict:
        return self._json_data


class FakeClient:
    def __init__(self, login_html: str) -> None:
        self.login_html = login_html
        self.cookies = FakeCookies()
        self.get_calls: list[str] = []
        self.post_calls: list[tuple[str, dict]] = []
        self.closed = False

    def get(self, url: str, **kwargs) -> FakeResponse:
        self.get_calls.append(url)
        if len(self.get_calls) == 1:
            return FakeResponse(text=self.login_html)
        self.cookies.set("client.oauth2_token", "tongji-token")
        return FakeResponse(text="ok")

    def post(self, url: str, *, headers=None, json=None) -> FakeResponse:
        self.post_calls.append((url, json or {}))
        redirect = (
            "https://yunpan.tongji.edu.cn:443/oauth2/auth"
            "?login_verifier=test-verifier"
        )
        return FakeResponse(
            text=_json.dumps({"redirect": redirect}),
            json_data={"redirect": redirect},
        )

    def close(self) -> None:
        self.closed = True


def _login_html() -> str:
    props = {
        "props": {
            "pageProps": {
                "challenge": "challenge-value",
                "csrftoken": "csrf-value",
                "device": {
                    "name": "",
                    "description": "",
                    "client_type": "web",
                    "udids": [],
                },
            }
        }
    }
    return (
        '<script id="__NEXT_DATA__" type="application/json">'
        + _json.dumps(props, ensure_ascii=False)
        + "</script>"
    )


def test_uses_tongji_login_matches_tongji_host() -> None:
    assert auth._uses_tongji_login("https://yunpan.tongji.edu.cn:443/")
    assert auth._uses_tongji_login("https://YUNPAN.TONGJI.EDU.CN/")
    assert not auth._uses_tongji_login("https://bhpan.buaa.edu.cn:443/")


def test_extract_tongji_login_props_rejects_missing_next_data() -> None:
    with pytest.raises(RuntimeError, match="__NEXT_DATA__"):
        auth._extract_tongji_login_props("<html></html>")


def test_get_access_token_dispatches_tongji_flow(monkeypatch) -> None:
    calls: list[tuple[str, str, str, bool]] = []

    def fake_tongji(
        base_url: str,
        username: str,
        encrypted_password: str,
        *,
        verify_tls: bool,
    ) -> str:
        calls.append((base_url, username, encrypted_password, verify_tls))
        return "tongji-token"

    monkeypatch.setattr(auth, "_get_tongji_access_token", fake_tongji)

    token = auth.get_access_token(
        "https://yunpan.tongji.edu.cn:443/",
        "student-id",
        "encrypted-password",
        verify_tls=False,
    )

    assert token == "tongji-token"
    assert calls == [
        (
            "https://yunpan.tongji.edu.cn:443/",
            "student-id",
            "encrypted-password",
            False,
        )
    ]


def test_get_tongji_access_token_uses_web_login_flow(monkeypatch) -> None:
    client = FakeClient(_login_html())
    monkeypatch.setattr(
        "pansh.auth.network.create_client",
        lambda **kwargs: client,
    )

    token = auth._get_tongji_access_token(
        "https://yunpan.tongji.edu.cn:443/",
        "student-id",
        "encrypted-password",
        verify_tls=False,
    )

    assert token == "tongji-token"
    assert client.closed is True
    assert client.get_calls[0].startswith(
        "https://yunpan.tongji.edu.cn:443/anyshare/oauth2/login?"
    )
    assert client.get_calls[1].startswith(
        "https://yunpan.tongji.edu.cn:443/oauth2/auth"
    )

    signin_url, signin_body = client.post_calls[0]
    assert signin_url == "https://yunpan.tongji.edu.cn:443/oauth2/signin"
    assert signin_body["_csrf"] == "csrf-value"
    assert signin_body["challenge"] == "challenge-value"
    assert signin_body["account"] == "student-id"
    assert signin_body["password"] == "encrypted-password"
    assert signin_body["device"]["client_type"] == "web"
