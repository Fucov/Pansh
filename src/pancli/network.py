"""HTTP transport helpers for PanCLI."""

from __future__ import annotations

import asyncio
import logging
import ssl
import time
from typing import Any, AsyncIterator

import httpx
from httpx import Timeout

from .config import CERT_FILE, get_data_dir
from .settings import load_settings

logger = logging.getLogger(__name__)


class ApiException(Exception):
    def __init__(self, err: dict | None, *args: object) -> None:
        super().__init__(*args)
        self.err = err


_MISSING_CERT_PEM = """\
-----BEGIN CERTIFICATE-----
MIIDXzCCAkegAwIBAgILBAAAAAABIVhTCKIwDQYJKoZIhvcNAQELBQAwTDEgMB4G
A1UECxMXR2xvYmFsU2lnbiBSb290IENBIC0gUjMxEzARBgNVBAoTCkdsb2JhbFNp
Z24xEzARBgNVBAMTCkdsb2JhbFNpZ24wHhcNMDkwMzE4MTAwMDAwWhcNMjkwMzE4
MTAwMDAwWjBMMSAwHgYDVQQLExdHbG9iYWxTaWduIFJvb3QgQ0EgLSBSMzETMBEG
A1UEChMKR2xvYmFsU2lnbjETMBEGA1UEAxMKR2xvYmFsU2lnbjCCASIwDQYJKoZI
hvcNAQEBBQADggEPADCCAQoCggEBAMwldpB5BngiFvXAg7aEyiie/QV2EcWtiHL8
RgJDx7KKnQRfJMsuS+FggkbhUqsMgUdwbN1k0ev1LKMPgj0MK66X17YUhhB5uzsT
gHeMCOFJ0mpiLx9e+pZo34knlTifBtc+ycsmWQ1z3rDI6SYOgxXG71uL0gRgykmm
KPZpO/bLyCiR5Z2KYVc3rHQU3HTgOu5yLy6c+9C7v/U9AOEGM+iCK65TpjoWc4zd
QQ4gOsC0p6Hpsk+QLjJg6VfLuQSSaGjlOCZgdbKfd/+RFO+uIEn8rUAVSNECMWEZ
XriX7613t2Saer9fwRPvm2L7DWzgVGkWqQPabumDk3F2xmmFghcCAwEAAaNCMEAw
DgYDVR0PAQH/BAQDAgEGMA8GA1UdEwEB/wQFMAMBAf8wHQYDVR0OBBYEFI/wS3+o
LkUkrk1Q+mOai97i3Ru8MA0GCSqGSIb3DQEBCwUAA4IBAQBLQNvAUKr+yAzv95ZU
RUm7lgAJQayzE4aGKAczymvmdLm6AC2upArT9fHxD4q/c2dKg8dEe3jgr25sbwMp
jjM5RcOO5LlXbKr8EpbsU8Yt5CRsuZRj+9xTaGdWPoO4zzUhw8lo/s7awlOqzJCK
6fBdRoyV3XpYKBovHd7NADdBj+1EbddTKJd+82cEHhXXipa0095MJ6RMG3NzdvQX
mcIfeg7jLQitChws/zyrVQ4PkX4268NXSb7hLi18YIvDQVETI53O9zJrlAGomecs
Mx86OyXShkDOOyyGeMlhLxS67ttVb9+E7gUJTb0o2HLO02JQZR7rkpeDMdmztcpH
WD9f
-----END CERTIFICATE-----
"""


def _ensure_cert() -> str:
    get_data_dir().mkdir(parents=True, exist_ok=True)
    if not CERT_FILE.exists():
        CERT_FILE.write_text(_MISSING_CERT_PEM, encoding="utf-8")
    return str(CERT_FILE)


def _build_ssl_context() -> ssl.SSLContext:
    context = ssl.create_default_context()
    context.load_verify_locations(_ensure_cert())
    return context


def _timeout() -> Timeout:
    settings = load_settings()
    return Timeout(
        settings.request_timeout,
        connect=settings.connect_timeout,
        read=settings.read_timeout,
    )


def create_client(**kwargs: Any) -> httpx.Client:
    return httpx.Client(verify=_build_ssl_context(), timeout=_timeout(), **kwargs)


def create_async_client(**kwargs: Any) -> httpx.AsyncClient:
    return httpx.AsyncClient(verify=_build_ssl_context(), timeout=_timeout(), **kwargs)


def post_json(
    url: str,
    json_obj: Any,
    *,
    tokenid: str | None = None,
    client: httpx.Client | None = None,
) -> dict | list | None:
    headers = {"Content-Type": "application/json"}
    if tokenid:
        headers["Authorization"] = f"Bearer {tokenid}"
    own_client = client is None
    sync_client = client or create_client()
    settings = load_settings()
    last_error: Exception | None = None
    try:
        for attempt in range(settings.max_retries):
            try:
                response = sync_client.post(url, headers=headers, json=json_obj)
                _raise_for_status(response)
                if not response.text:
                    return None
                return response.json()
            except (httpx.ConnectError, httpx.ReadTimeout, httpx.RemoteProtocolError) as exc:
                last_error = exc
                if attempt + 1 >= settings.max_retries:
                    raise
                time.sleep(settings.retry_backoff)
    finally:
        if own_client:
            sync_client.close()
    if last_error:
        raise last_error
    return None


def get_json(
    url: str,
    *,
    tokenid: str | None = None,
    client: httpx.Client | None = None,
) -> dict | list | None:
    headers: dict[str, str] = {}
    if tokenid:
        headers["Authorization"] = f"Bearer {tokenid}"
    own_client = client is None
    sync_client = client or create_client()
    settings = load_settings()
    last_error: Exception | None = None
    try:
        for attempt in range(settings.max_retries):
            try:
                response = sync_client.get(url, headers=headers)
                _raise_for_status(response)
                if not response.text:
                    return None
                return response.json()
            except (httpx.ConnectError, httpx.ReadTimeout, httpx.RemoteProtocolError) as exc:
                last_error = exc
                if attempt + 1 >= settings.max_retries:
                    raise
                time.sleep(settings.retry_backoff)
    finally:
        if own_client:
            sync_client.close()
    if last_error:
        raise last_error
    return None


async def _with_retry(requester, *, retries: int, backoff: float):
    last_error: Exception | None = None
    for attempt in range(retries):
        try:
            return await requester()
        except (httpx.ConnectError, httpx.ReadTimeout, httpx.RemoteProtocolError) as exc:
            last_error = exc
            if attempt + 1 >= retries:
                raise
            await asyncio.sleep(backoff)
    if last_error:
        raise last_error
    raise RuntimeError("retry loop exited unexpectedly")


def _raise_for_status(response: httpx.Response) -> None:
    if response.status_code in (200, 201):
        return
    err = None
    try:
        err = response.json()
    except Exception:
        err = None
    raise ApiException(err, f"api returned HTTP {response.status_code}: {response.text}")


async def async_post_json(
    url: str,
    json_obj: Any,
    *,
    tokenid: str | None = None,
    client: httpx.AsyncClient | None = None,
) -> dict | list | None:
    headers = {"Content-Type": "application/json"}
    if tokenid:
        headers["Authorization"] = f"Bearer {tokenid}"
    own_client = client is None
    async_client = client or create_async_client()
    settings = load_settings()
    started = time.perf_counter()
    try:
        response = await _with_retry(
            lambda: async_client.post(url, headers=headers, json=json_obj),
            retries=settings.max_retries,
            backoff=settings.retry_backoff,
        )
        _raise_for_status(response)
        logger.debug("POST %s finished in %.3fs", url, time.perf_counter() - started)
        if not response.text:
            return None
        return response.json()
    finally:
        if own_client:
            await async_client.aclose()


async def async_get_json(
    url: str,
    *,
    tokenid: str | None = None,
    client: httpx.AsyncClient | None = None,
) -> dict | list | None:
    headers: dict[str, str] = {}
    if tokenid:
        headers["Authorization"] = f"Bearer {tokenid}"
    own_client = client is None
    async_client = client or create_async_client()
    settings = load_settings()
    started = time.perf_counter()
    try:
        response = await _with_retry(
            lambda: async_client.get(url, headers=headers),
            retries=settings.max_retries,
            backoff=settings.retry_backoff,
        )
        _raise_for_status(response)
        logger.debug("GET %s finished in %.3fs", url, time.perf_counter() - started)
        if not response.text:
            return None
        return response.json()
    finally:
        if own_client:
            await async_client.aclose()


async def async_put_file(
    url: str,
    headers: dict[str, str],
    content: bytes | Any,
    *,
    client: httpx.AsyncClient | None = None,
) -> None:
    own_client = client is None
    async_client = client or create_async_client()
    settings = load_settings()
    try:
        response = await _with_retry(
            lambda: async_client.put(url, headers=headers, content=content),
            retries=settings.max_retries,
            backoff=settings.retry_backoff,
        )
        _raise_for_status(response)
    finally:
        if own_client:
            await async_client.aclose()


async def async_get_file(url: str, *, client: httpx.AsyncClient | None = None) -> bytes:
    own_client = client is None
    async_client = client or create_async_client()
    try:
        response = await async_client.get(url)
        _raise_for_status(response)
        return response.content
    finally:
        if own_client:
            await async_client.aclose()


async def async_stream_download(
    url: str,
    *,
    headers: dict[str, str] | None = None,
    client: httpx.AsyncClient | None = None,
    chunk_size: int | None = None,
) -> AsyncIterator[bytes]:
    own_client = client is None
    async_client = client or create_async_client()
    chunk = chunk_size or load_settings().chunk_size
    try:
        async with async_client.stream("GET", url, headers=headers or {}) as response:
            _raise_for_status(response)
            async for item in response.aiter_bytes(chunk):
                yield item
    finally:
        if own_client:
            await async_client.aclose()
