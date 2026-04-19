"""Async AnyShare API wrapper used by the CLI and shell."""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, AsyncIterator

from . import auth, network
from .models import DirEntry, FileMetaData, LinkInfo, QuotaInfo, ResourceInfo, RevisionInfo, SearchResult

logger = logging.getLogger(__name__)


class ApiManagerException(Exception):
    pass


class WrongPasswordException(ApiManagerException):
    pass


class InvalidRootException(ApiManagerException):
    pass


class NeedReviewException(ApiManagerException):
    pass


class MoveToChildDirectoryException(ApiManagerException):
    pass


class AsyncApiManager:
    def __init__(
        self,
        host: str,
        username: str,
        password: str | None,
        pubkey: str,
        *,
        encrypted: str | None = None,
        cached_token: str | None = None,
        cached_expire: float | None = None,
    ) -> None:
        self.host = host
        self.base_url = f"https://{host}:443/api/efast/v1"
        self._pubkey = pubkey
        self._password = password
        self._username = username
        self._encrypted = encrypted
        self._tokenid = cached_token or ""
        self._expires = cached_expire or 0.0
        self._client: network.httpx.AsyncClient | None = None

    @property
    def client(self) -> network.httpx.AsyncClient:
        if self._client is None:
            self._client = network.create_async_client()
        return self._client

    async def initialize(self) -> None:
        await self._check_token(use_request=True)

    async def close(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    def _url(self, path: str) -> str:
        if not path.startswith("/"):
            path = "/" + path
        return self.base_url + path

    def _encrypt_password(self) -> str:
        if self._encrypted is None and self._password is not None:
            self._encrypted = auth.rsa_encrypt(self._password, self._pubkey)
        return self._encrypted or ""

    async def _update_token(self) -> None:
        started = time.perf_counter()
        try:
            self._tokenid = await asyncio.to_thread(
                auth.get_access_token,
                f"https://{self.host}:443/",
                self._username,
                self._encrypt_password(),
            )
            self._expires = time.time() + 3600
        except network.ApiException as exc:
            if exc.err and exc.err.get("code") == 401001003:
                raise WrongPasswordException(str(exc)) from exc
            raise
        finally:
            logger.debug("token refresh took %.3fs", time.perf_counter() - started)

    async def _check_token(self, use_request: bool = False) -> None:
        if time.time() < (self._expires - 60) and self._tokenid:
            if not use_request:
                return
        if not self._tokenid or time.time() >= (self._expires - 60):
            await self._update_token()
            return
        if use_request:
            started = time.perf_counter()
            try:
                await self.get_entrydoc()
            except network.ApiException as exc:
                if exc.err and exc.err.get("code") == 401001001:
                    await self._update_token()
                else:
                    raise
            finally:
                logger.debug("first request took %.3fs", time.perf_counter() - started)

    async def _post(self, path: str, body: dict[str, Any]) -> Any:
        await self._check_token()
        return await network.async_post_json(
            self._url(path),
            body,
            tokenid=self._tokenid,
            client=self.client,
        )

    async def _get(self, path: str) -> Any:
        await self._check_token()
        return await network.async_get_json(
            self._url(path),
            tokenid=self._tokenid,
            client=self.client,
        )

    async def get_entrydoc(self) -> list[dict]:
        data = await self._get("/entry-doc-lib?type=user_doc_lib&sort=doc_lib_name&direction=asc")
        return data or []

    async def get_current_user(self) -> dict[str, Any]:
        return await self._post("/../../eacp/v1/user/get", {})

    async def get_quota(self) -> QuotaInfo:
        data = await self._get("/quota/user")
        return QuotaInfo.model_validate(data or {})

    async def get_resource_info_by_path(self, path: str) -> ResourceInfo | None:
        path = path.strip("/")
        if not path:
            return None
        try:
            data = await self._post("/file/getinfobypath", {"namepath": path})
        except network.ApiException as exc:
            if exc.err and exc.err.get("code") in (404006, 403024, 404002006, 400002015):
                return None
            raise
        return ResourceInfo.model_validate(data) if data else None

    async def get_resource_id(self, path: str) -> str | None:
        info = await self.get_resource_info_by_path(path)
        return info.docid if info else None

    async def get_resource_path(self, docid: str) -> str:
        data = await self._post("/file/convertpath", {"docid": docid})
        return data["namepath"]

    async def get_file_meta(self, file_id: str) -> FileMetaData:
        data = await self._post("/file/metadata", {"docid": file_id})
        return FileMetaData.model_validate(data)

    async def list_dir(
        self,
        dir_id: str,
        *,
        by: str | None = None,
        sort: str | None = None,
        with_attr: bool = False,
    ) -> tuple[list[DirEntry], list[DirEntry]]:
        payload: dict[str, Any] = {"docid": dir_id, "attr": bool(with_attr)}
        if by:
            payload["by"] = by
        if sort:
            payload["sort"] = sort
        data = await self._post("/dir/list", payload)
        data = data or {}
        dirs = [DirEntry.from_dict(item, is_dir=True) for item in data.get("dirs", [])]
        files = [DirEntry.from_dict(item, is_dir=False) for item in data.get("files", [])]
        return dirs, files

    async def create_dir(self, parent_dir_id: str, name: str) -> str:
        data = await self._post("/dir/create", {"docid": parent_dir_id, "name": name})
        return data["docid"]

    async def create_dirs(self, parent_dir_id: str, dirs: str) -> str:
        data = await self._post("/dir/createmultileveldir", {"docid": parent_dir_id, "path": dirs})
        return data["docid"]

    async def create_dirs_by_path(self, dirs: str) -> str:
        parts = [chunk for chunk in dirs.strip("/").split("/") if chunk]
        if not parts:
            raise InvalidRootException("empty remote path")
        root_id = await self.get_resource_id(parts[0])
        if root_id is None:
            raise InvalidRootException("root dir does not exist")
        if len(parts) == 1:
            return root_id
        return await self.create_dirs(root_id, "/".join(parts[1:]))

    async def delete_file(self, file_id: str) -> None:
        await self._post("/file/delete", {"docid": file_id})

    async def delete_dir(self, dir_id: str) -> None:
        await self._post("/dir/delete", {"docid": dir_id})

    async def rename_file(self, file_id: str, new_name: str, *, rename_on_dup: bool = False) -> str | None:
        data = await self._post(
            "/file/rename",
            {"docid": file_id, "name": new_name, "ondup": 2 if rename_on_dup else 1},
        )
        return data["name"] if rename_on_dup else None

    async def move_file(
        self,
        file_id: str,
        dest_dir_id: str,
        *,
        rename_on_dup: bool = False,
        overwrite_on_dup: bool = False,
    ) -> str | tuple[str, str]:
        ondup = 2 if rename_on_dup else (3 if overwrite_on_dup else 1)
        try:
            data = await self._post("/file/move", {"docid": file_id, "destparent": dest_dir_id, "ondup": ondup})
        except network.ApiException as exc:
            if exc.err and exc.err.get("errcode") == 403019:
                raise MoveToChildDirectoryException() from exc
            raise
        if rename_on_dup:
            return data["docid"], data["name"]
        return data["docid"]

    async def copy_file(
        self,
        file_id: str,
        dest_dir_id: str,
        *,
        rename_on_dup: bool = False,
        overwrite_on_dup: bool = False,
    ) -> str | tuple[str, str]:
        ondup = 2 if rename_on_dup else (3 if overwrite_on_dup else 1)
        try:
            data = await self._post("/file/copy", {"docid": file_id, "destparent": dest_dir_id, "ondup": ondup})
        except network.ApiException as exc:
            if exc.err and exc.err.get("errcode") == 403019:
                raise MoveToChildDirectoryException() from exc
            raise
        if rename_on_dup:
            return data["docid"], data["name"]
        return data["docid"]

    async def get_download_url(self, file_id: str) -> tuple[str, int]:
        data = await self._post("/file/osdownload", {"docid": file_id, "authtype": "QUERY_STRING"})
        return data["authrequest"][1], int(data.get("length", 0))

    async def download_file_stream(self, file_id: str, *, resume_from: int = 0) -> AsyncIterator[bytes]:
        url, _ = await self.get_download_url(file_id)
        headers = {"Range": f"bytes={resume_from}-"} if resume_from > 0 else {}
        async for chunk in network.async_stream_download(url, headers=headers, client=self.client):
            yield chunk

    async def upload_file(
        self,
        parent_dir_id: str,
        name: str,
        content: bytes | Any,
        *,
        check_existence: bool = True,
        stream_len: int | None = None,
    ) -> str:
        edit_mode = False
        existing_file_id: str | None = None
        if check_existence:
            parent_dir = await self.get_resource_path(parent_dir_id)
            existing_file_id = await self.get_resource_id(parent_dir + "/" + name)
            edit_mode = existing_file_id is not None
        upload_info = await self._post(
            "/file/osbeginupload",
            {
                "docid": existing_file_id if edit_mode else parent_dir_id,
                "length": stream_len if stream_len is not None else len(content),
                "name": None if edit_mode else name,
                "reqmethod": "PUT",
            },
        )
        headers: dict[str, str] = {}
        for raw_header in upload_info["authrequest"][2:]:
            key, _, value = raw_header.partition(": ")
            if key and value:
                headers[key] = value
        await network.async_put_file(upload_info["authrequest"][1], headers, content, client=self.client)
        await self._post("/file/osendupload", {"docid": upload_info["docid"], "rev": upload_info["rev"]})
        return upload_info["docid"]

    async def get_link(self, docid: str) -> LinkInfo | None:
        data = await self._post("/link/getdetail", {"docid": docid})
        if not data or not data.get("link"):
            return None
        return LinkInfo.model_validate(data)

    async def create_link(
        self,
        docid: str,
        end_time: int | None = None,
        limit_times: int = -1,
        *,
        enable_pass: bool = False,
        allow_view: bool = True,
        allow_download: bool = True,
        allow_upload: bool = False,
    ) -> LinkInfo:
        if allow_download:
            allow_view = True
        perm = int(allow_view) + 2 * int(allow_download) + 4 * int(allow_upload)
        payload: dict[str, Any] = {
            "docid": docid,
            "open": enable_pass,
            "limittimes": limit_times,
            "perm": perm,
        }
        if end_time is not None:
            payload["endtime"] = end_time
        data = await self._post("/link/open", payload)
        if data and data.get("result") == 0:
            return LinkInfo.model_validate(data)
        raise NeedReviewException()

    async def delete_link(self, docid: str) -> None:
        await self._post("/link/close", {"docid": docid})

    async def search_recursive(
        self,
        dir_id: str,
        keyword: str,
        *,
        max_depth: int = 3,
        current_depth: int = 0,
        base_path: str = "",
    ) -> list[SearchResult]:
        if current_depth >= max_depth:
            return []
        dirs, files = await self.list_dir(dir_id, by="name")
        lowered = keyword.lower()
        results: list[SearchResult] = []
        for directory in dirs:
            path = f"{base_path}/{directory.name}".strip("/")
            if lowered in directory.name.lower():
                results.append(
                    SearchResult(
                        path=path,
                        name=directory.name,
                        size=directory.size,
                        modified=directory.modified,
                        is_dir=True,
                    )
                )
            results.extend(
                await self.search_recursive(
                    directory.docid,
                    keyword,
                    max_depth=max_depth,
                    current_depth=current_depth + 1,
                    base_path=path,
                )
            )
        for file in files:
            if lowered in file.name.lower():
                path = f"{base_path}/{file.name}".strip("/")
                results.append(
                    SearchResult(
                        path=path,
                        name=file.name,
                        size=file.size,
                        modified=file.modified,
                        is_dir=False,
                    )
                )
        return results

    async def search(self, root_path: str, keyword: str, *, max_depth: int = 3) -> list[SearchResult]:
        root_id = await self.get_resource_id(root_path.strip("/"))
        if root_id is None:
            return []
        return await self.search_recursive(root_id, keyword, max_depth=max_depth, base_path=root_path.strip("/"))

    async def get_revisions(self, file_id: str) -> list[RevisionInfo]:
        data = await self._post("/file/revisions", {"docid": file_id})
        return [RevisionInfo.model_validate(item) for item in (data or [])]

    async def restore_revision(self, file_id: str, rev: str) -> dict[str, Any] | None:
        return await self._post("/file/restorerevision", {"docid": file_id, "rev": rev})
