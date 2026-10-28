"""Async client for the parts of Kavita's REST API kavita-mcp uses (Kavita 0.9.1).

Auth is the key in the x-api-key header on every call. Request bodies and
enum numbers were checked against a live 0.9.1.4.
"""

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime

import httpx

from kavita_mcp.errors import KavitaMcpError

# Series/v2 filter fields (SeriesFilterV2Dto, integers only).
FIELD_PATH = 24
EQUAL = 0
COMBINE_AND = 1
SORT_ADDED = 2


class KavitaError(KavitaMcpError):
    pass


class KavitaUnreachable(KavitaError):
    pass


class KavitaAuthError(KavitaError):
    pass


class KavitaForbidden(KavitaError):
    pass


class KavitaNotFound(KavitaError):
    pass


class KavitaBadRequest(KavitaError):
    pass


@dataclass
class Page:
    items: list[dict]
    total: int


class KavitaClient:
    def __init__(
        self,
        base_url: str,
        api_key: str,
        *,
        http: httpx.AsyncClient | None = None,
        timeout: float = 15.0,
    ):
        self._base = base_url.rstrip("/")
        self._key = api_key
        self._http = http or httpx.AsyncClient(timeout=timeout)

    async def aclose(self) -> None:
        await self._http.aclose()

    async def _request(
        self, method: str, path: str, *, params: dict | None = None, json_body=None
    ) -> httpx.Response:
        try:
            r = await self._http.request(
                method,
                f"{self._base}{path}",
                params=params,
                json=json_body,
                headers={"x-api-key": self._key},
            )
        except httpx.TransportError as e:
            raise KavitaUnreachable(f"Kavita is unreachable at {self._base}: {e}") from None
        if r.status_code == 401:
            raise KavitaAuthError("Kavita rejected the API key: check KAVITA_API_KEY")
        if r.status_code == 403:
            raise KavitaForbidden("Kavita refused this: it needs an admin key")
        if r.status_code == 404:
            raise KavitaNotFound(f"Kavita has no such item ({path})")
        if r.status_code == 400:
            raise KavitaBadRequest(f"Kavita refused the request: {r.text[:200]}")
        if r.is_error:
            raise KavitaError(f"Kavita answered {r.status_code} for {path}")
        return r

    async def _json(self, method: str, path: str, **kwargs):
        r = await self._request(method, path, **kwargs)
        return r.json() if r.content else None

    @staticmethod
    def _page(r: httpx.Response) -> Page:
        items = r.json() if r.content else []
        pagination = r.headers.get("Pagination")
        total = json.loads(pagination)["totalItems"] if pagination else len(items)
        return Page(items=items, total=total)

    async def health(self) -> bool:
        try:
            await self._request("GET", "/api/health")
        except KavitaError:
            return False
        return True

    async def libraries(self) -> list[dict]:
        return await self._json("GET", "/api/Library/libraries")

    async def series(self, series_id: int) -> dict:
        return await self._json("GET", f"/api/Series/{series_id}")

    async def metadata(self, series_id: int) -> dict:
        return await self._json("GET", "/api/Series/metadata", params={"seriesId": series_id})

    async def detail(self, series_id: int) -> dict:
        return await self._json("GET", "/api/Series/series-detail", params={"seriesId": series_id})

    async def filter_series(
        self,
        statements: list[dict],
        *,
        sort_field: int = SORT_ADDED,
        ascending: bool = False,
        page: int = 1,
        page_size: int = 25,
    ) -> Page:
        body = {
            "statements": statements,
            "combination": COMBINE_AND,
            "sortOptions": {"sortField": sort_field, "isAscending": ascending},
            "limitTo": 0,
        }
        r = await self._request(
            "POST",
            "/api/Series/v2",
            params={"PageNumber": page, "PageSize": page_size},
            json_body=body,
        )
        return self._page(r)

    async def series_by_folder(self, folder: str) -> dict | None:
        page = await self.filter_series(
            [{"field": FIELD_PATH, "comparison": EQUAL, "value": folder}], page_size=1
        )
        return page.items[0] if page.items else None

    async def search(self, query: str) -> list[dict]:
        body = await self._json(
            "GET",
            "/api/Search/search",
            params={"queryString": query, "includeChapterAndFiles": "false"},
        )
        return (body or {}).get("series") or []

    async def on_deck(self, *, page_size: int) -> Page:
        r = await self._request(
            "POST", "/api/Series/on-deck", params={"PageNumber": 1, "PageSize": page_size}
        )
        return self._page(r)

    async def want_to_read_list(self, *, page_size: int) -> Page:
        r = await self._request(
            "POST",
            "/api/want-to-read/v2",
            params={"PageNumber": 1, "PageSize": page_size},
            json_body={"statements": [], "combination": COMBINE_AND, "limitTo": 0},
        )
        return self._page(r)

    async def is_want_to_read(self, series_id: int) -> bool:
        return bool(await self._json("GET", "/api/want-to-read", params={"seriesId": series_id}))

    async def set_want_to_read(self, series_id: int, add: bool) -> None:
        path = "/api/want-to-read/add-series" if add else "/api/want-to-read/remove-series"
        await self._request("POST", path, json_body={"seriesIds": [series_id]})

    async def mark_read(self, series_id: int) -> None:
        await self._request("POST", "/api/Reader/mark-read", json_body={"seriesId": series_id})

    async def collections(self, *, owned_only: bool = False) -> list[dict]:
        return await self._json(
            "GET", "/api/Collection", params={"ownedOnly": str(owned_only).lower()}
        )

    async def collections_for_series(self, series_id: int) -> list[dict]:
        return await self._json("GET", "/api/Collection/all-series", params={"seriesId": series_id})

    async def _own_collection(self, title: str) -> dict | None:
        wanted = title.strip().casefold()
        for c in await self.collections(owned_only=True):
            if (c.get("title") or "").strip().casefold() == wanted:
                return c
        return None

    async def add_to_collection(self, series_id: int, title: str) -> int:
        """Add a series to the caller's collection with this title, creating it if needed.

        Kavita's id 0 always creates a new collection, even with a name that
        exists, so an existing one is looked up first (case-insensitively).
        """
        existing = await self._own_collection(title)
        body = {
            "collectionTagId": existing["id"] if existing else 0,
            "collectionTagTitle": existing["title"] if existing else title.strip(),
            "seriesIds": [series_id],
        }
        await self._request("POST", "/api/Collection/update-for-series", json_body=body)
        if existing:
            return existing["id"]
        created = await self._own_collection(title)
        if created is None:
            raise KavitaError(f"Kavita did not create the collection {title!r}")
        return created["id"]

    async def remove_from_collection(self, series_id: int, title: str) -> None:
        existing = await self._own_collection(title)
        if existing is None:
            return
        body = {
            "tag": {"id": existing["id"], "title": existing["title"]},
            "seriesIdsToRemove": [series_id],
        }
        await self._request("POST", "/api/Collection/update-series", json_body=body)

    async def scan_library(self, library_id: int) -> None:
        await self._request("POST", "/api/Library/scan", params={"libraryId": library_id})

    async def scan_series(self, library_id: int, series_id: int) -> None:
        """Re-read one series' files now (admin). Kavita always forces this scan."""
        await self._request(
            "POST",
            "/api/Series/scan",
            json_body={"libraryId": library_id, "seriesId": series_id, "forceUpdate": True},
        )

    async def server_time(self) -> datetime:
        """Kavita's clock, from the Date header of a cheap request."""
        r = await self._request("GET", "/api/health")
        date = r.headers.get("Date")
        return parsedate_to_datetime(date) if date else datetime.now(UTC)

    async def delete_series(self, series_id: int) -> None:
        """Remove a series from Kavita (admin). Only once its files are gone: a scan
        would bring it back otherwise."""
        await self._request("DELETE", f"/api/Series/{series_id}")
