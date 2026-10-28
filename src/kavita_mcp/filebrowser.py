"""Async client for File Browser's HTTP API (v2.63, login method "noauth").

kavita-mcp writes books through File Browser instead of mounting the library:
File Browser owns the folder (Podman re-owns it to File Browser's user namespace
at every start) and makes files 0644 and folders 0755, which Kavita needs.
"""

import asyncio
from urllib.parse import quote

import httpx

from kavita_mcp.errors import KavitaMcpError


class UploadServiceError(KavitaMcpError):
    pass


class UploadUnreachable(UploadServiceError):
    pass


class FileBrowserClient:
    def __init__(
        self, base_url: str, *, http: httpx.AsyncClient | None = None, timeout: float = 60.0
    ):
        self._base = base_url.rstrip("/")
        self._http = http or httpx.AsyncClient(timeout=timeout)
        self._token: str | None = None
        self._login_lock = asyncio.Lock()

    async def aclose(self) -> None:
        await self._http.aclose()

    async def _send(self, method: str, url: str, **kwargs) -> httpx.Response:
        try:
            return await self._http.request(method, url, **kwargs)
        except httpx.TransportError as e:
            raise UploadUnreachable(
                f"The upload service is unreachable at {self._base}: {e}"
            ) from None

    async def _login(self, stale: str | None) -> str:
        async with self._login_lock:
            if self._token is None or self._token == stale:
                # noauth: an empty login returns a token (2 h) for File Browser's only user.
                r = await self._send("POST", f"{self._base}/api/login", content=b"{}")
                if r.status_code != 200:
                    raise UploadServiceError(
                        f"The upload service refused the login ({r.status_code})"
                    )
                self._token = r.text.strip()
            return self._token

    async def _request(self, method: str, api: str, path: str, **kwargs) -> httpx.Response:
        url = f"{self._base}/api/{api}{quote(path)}"
        token = await self._login(None)
        r = await self._send(method, url, headers={"X-Auth": token}, **kwargs)
        if r.status_code == 401:  # token expired: log in again, once
            token = await self._login(token)
            r = await self._send(method, url, headers={"X-Auth": token}, **kwargs)
            if r.status_code == 401:
                raise UploadServiceError("The upload service keeps refusing the login token")
        return r

    @staticmethod
    def _fail(r: httpx.Response, what: str) -> None:
        raise UploadServiceError(f"The upload service failed to {what} ({r.status_code})")

    async def upload(self, path: str, data: bytes) -> None:
        r = await self._request(
            "POST", "resources", path, params={"override": "true"}, content=data
        )
        if r.status_code != 200:
            self._fail(r, f"write {path}")

    async def download(self, path: str) -> bytes:
        r = await self._request("GET", "raw", path)
        if r.status_code != 200:
            self._fail(r, f"read {path}")
        return r.content

    async def delete(self, path: str) -> bool:
        r = await self._request("DELETE", "resources", path)
        if r.status_code == 404:
            return False
        if r.status_code not in (200, 204):
            self._fail(r, f"delete {path}")
        return True

    async def exists(self, path: str) -> bool:
        r = await self._request("GET", "resources", path)
        if r.status_code == 404:
            return False
        if r.status_code != 200:
            self._fail(r, f"look up {path}")
        return True

    async def list_dir(self, path: str) -> list[str]:
        r = await self._request("GET", "resources", path)
        if r.status_code != 200:
            self._fail(r, f"list {path}")
        return [item["name"] for item in r.json().get("items") or []]
