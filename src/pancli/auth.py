"""Authentication layer — OAuth2 login flow + RSA encryption."""

from __future__ import annotations

import base64
import re
import urllib.parse

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
    base_url = base_url.rstrip("/")
    state = urllib.parse.quote(base64.b64encode(b'{"windowId":3}'))

    client = network.create_client(follow_redirects=True)
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
