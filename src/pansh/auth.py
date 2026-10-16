"""Authentication layer — OAuth2 login flow + RSA encryption."""

from __future__ import annotations

import base64
import json
import re
import urllib.parse
from typing import Any

import rsa as _rsa

from . import network

# ── RSA 加密（保留原有逻辑） ────────────────────────────────────


def rsa_encrypt(message: str, public_key: str) -> str:
    """RSA + base64 加密，返回 base64 编码的密文字符串。"""
    pubkey = _rsa.PublicKey.load_pkcs1_openssl_pem(public_key)
    crypto = _rsa.encrypt(message.encode(), pubkey)
    return base64.b64encode(crypto).decode()


# ── OAuth2 登录流程 ─────────────────────────────────────────────

_CLIENT_ID = "0f4bc444-d39a-4945-84a3-023d1f439148"
_BASIC_AUTH = "Basic MGY0YmM0NDQtZDM5YS00OTQ1LTg0YTMtMDIzZDFmNDM5MTQ4OnVOaVU0V0ZUd1FEfjE4T2JHMkU1M2dqN3ot"
_TONGJI_HOST = "yunpan.tongji.edu.cn"


def _extract_login_challenge(html: str) -> tuple[str, str]:
    """从登录页面 HTML 中提取 challenge 和 csrftoken。"""
    challenge = re.search(r'"challenge":"(.*?)"', html)
    csrf = re.search(r'"csrftoken":"(.*?)"', html)
    if not challenge or not csrf:
        raise RuntimeError("无法从登录页面提取 challenge / csrftoken")
    return challenge.group(1), csrf.group(1)


def _extract_code(url: str) -> str | None:
    """从 anyshare:// 回调 URL 中提取 authorization code。"""
    m = re.search(r"code=([^&]+)", url)
    return m.group(1) if m else None


def _uses_tongji_login(base_url: str) -> bool:
    """判断当前端点是否使用同济云盘的 Web 登录流程。"""
    hostname = (urllib.parse.urlparse(base_url).hostname or "").lower().rstrip(".")
    return hostname == _TONGJI_HOST


def _extract_tongji_login_props(html: str) -> tuple[str, str, dict[str, Any]]:
    """从同济云盘登录页的 __NEXT_DATA__ 中提取 challenge、csrftoken 和设备信息。"""
    match = re.search(
        r'<script[^>]*id="__NEXT_DATA__"[^>]*>(.*?)</script>',
        html,
        re.S,
    )
    if not match:
        raise RuntimeError("无法从同济云盘登录页提取 __NEXT_DATA__")
    next_data = json.loads(match.group(1))
    props = next_data["props"]["pageProps"]
    challenge = props["challenge"]
    csrf_token = props["csrftoken"]
    device = props.get("device") or {
        "name": "",
        "description": "",
        "client_type": "web",
        "udids": [],
    }
    return challenge, csrf_token, device


def _get_tongji_access_token(
    base_url: str,
    username: str,
    encrypted_password: str,
    *,
    verify_tls: bool,
) -> str:
    """走同济云盘 Web 登录流程，返回 access_token。"""
    base_url = base_url.rstrip("/")
    client = network.create_client(follow_redirects=True, verify_tls=verify_tls)
    try:
        login_query = urllib.parse.urlencode(
            {
                "lang": "zh-cn",
                "redirect": "/anyshare/zh-cn/homepage",
            }
        )
        login_resp = client.get(f"{base_url}/anyshare/oauth2/login?{login_query}")
        challenge, csrf_token, device = _extract_tongji_login_props(login_resp.text)

        signin_body = {
            "_csrf": csrf_token,
            "challenge": challenge,
            "account": username,
            "password": encrypted_password,
            "vcode": {"id": "", "content": ""},
            "dualfactorauthinfo": {
                "validcode": {"vcode": ""},
                "OTP": {"OTP": ""},
            },
            "remember": False,
            "device": device,
        }
        signin_resp = network.post_json(
            f"{base_url}/oauth2/signin",
            signin_body,
            client=client,
        )
        if not isinstance(signin_resp, dict) or "redirect" not in signin_resp:
            raise network.ApiException(
                signin_resp if isinstance(signin_resp, dict) else None,
                f"同济云盘登录失败: {signin_resp}",
            )

        client.get(str(signin_resp["redirect"]))
        token = client.cookies.get("client.oauth2_token")
        if not token:
            raise RuntimeError("同济云盘登录成功，但未获取到 client.oauth2_token")
        return token
    finally:
        client.close()


def _follow_redirects_until_anyshare(
    url: str,
    client: network.httpx.Client,
) -> network.httpx.Response:
    """跟踪 302 重定向直到遇到 anyshare:// scheme。"""
    while True:
        resp = client.get(url, follow_redirects=False)
        if resp.status_code in (301, 302, 303, 307, 308):
            new_url = resp.headers.get("Location", "")
            if "anyshare://" in new_url:
                return resp
            url = new_url
        else:
            return resp


def get_access_token(
    base_url: str,
    username: str,
    encrypted_password: str,
    *,
    verify_tls: bool = True,
) -> str:
    """
    完整 OAuth2 登录流程，返回 access_token。

    Parameters
    ----------
    base_url : str
        e.g. ``https://bhpan.buaa.edu.cn:443/``
    username : str
        学号 / 工号
    encrypted_password : str
        RSA + base64 加密后的密码
    """
    if _uses_tongji_login(base_url):
        return _get_tongji_access_token(
            base_url,
            username,
            encrypted_password,
            verify_tls=verify_tls,
        )

    base_url = base_url.rstrip("/")
    state = urllib.parse.quote(base64.b64encode(b'{"windowId":3}'))

    client = network.create_client(follow_redirects=True, verify_tls=verify_tls)
    try:
        # Step 1: 发起 OAuth2 授权请求，获取登录页面
        auth_url = (
            f"{base_url}/oauth2/auth?"
            f"audience=&client_id={_CLIENT_ID}"
            f"&redirect_uri=anyshare%3A%2F%2Foauth2%2Flogin%2Fcallback"
            f"&response_type=code&state={state}"
            f"&scope=offline+openid+all&lang=zh-cn"
            f"&udids=00-50-56-C0-00-01"
        )
        r = client.get(auth_url)
        challenge, csrf_token = _extract_login_challenge(r.text)

        # Step 2: 提交登录凭据
        signin_body = {
            "_csrf": csrf_token,
            "challenge": challenge,
            "account": username,
            "password": encrypted_password,
            "vcode": {"id": "", "content": ""},
            "dualfactorauthinfo": {
                "validcode": {"vcode": ""},
                "OTP": {"OTP": ""},
            },
            "remember": False,
            "device": {
                "name": "RichClient",
                "description": "RichClient for windows",
                "client_type": "windows",
                "udids": ["00-50-56-C0-00-01"],
            },
        }
        signin_resp = network.post_json(
            f"{base_url}/oauth2/signin",
            signin_body,
            client=client,
        )

        # Step 3: 跟踪重定向获取 authorization code
        redirect_url = signin_resp["redirect"]  # type: ignore[index]
        redir_resp = _follow_redirects_until_anyshare(redirect_url, client)

        location = redir_resp.headers.get("Location", "")
        code = _extract_code(location)
        if code is None:
            raise RuntimeError(f"无法从回调 URL 中提取 code: {location}")

        # Step 4: 用 code 换取 access_token
        boundary = "----WebKitFormBoundarywPAfbB36kbRTzgzy"
        token_body = (
            f"------WebKitFormBoundarywPAfbB36kbRTzgzy\r\n"
            f'Content-Disposition: form-data; name="grant_type"\r\n\r\n'
            f"authorization_code\r\n"
            f"------WebKitFormBoundarywPAfbB36kbRTzgzy\r\n"
            f'Content-Disposition: form-data; name="code"\r\n\r\n'
            f"{code}\r\n"
            f"------WebKitFormBoundarywPAfbB36kbRTzgzy\r\n"
            f'Content-Disposition: form-data; name="redirect_uri"\r\n\r\n'
            f"anyshare://oauth2/login/callback\r\n"
            f"------WebKitFormBoundarywPAfbB36kbRTzgzy--"
        )
        token_resp = client.post(
            f"{base_url}/oauth2/token",
            headers={
                "Content-Type": f"multipart/form-data; boundary={boundary}",
                "Authorization": _BASIC_AUTH,
            },
            content=token_body.encode(),
        )
        return token_resp.json()["access_token"]
    finally:
        client.close()
