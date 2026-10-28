"""Reading tools: list, search, details, want-to-read, progress. As the key's user."""

import asyncio

from kavita_mcp.config import Settings
from kavita_mcp.errors import KavitaMcpError
from kavita_mcp.kavita import KavitaClient
from kavita_mcp.rows import author_of, series_row, series_url

MAX_PAGE_SIZE = 100
MAX_LIMIT = 50
CONCURRENCY = 8

# Series/v2 filter fields and comparisons (Kavita 0.9.1, integers only).
FIELD_COLLECTION = 7
FIELD_LIBRARY = 19
FIELD_READ_PROGRESS = 20
CONTAINS = 5
EQUAL = 0
UNREAD = {"field": FIELD_READ_PROGRESS, "comparison": EQUAL, "value": "0"}
SORTS = {"added": (2, False), "title": (1, True), "read": (7, False)}
VIEWS = ("on_deck", "unread", "want_to_read")
ACTIONS = ("add", "remove")


class UnknownName(KavitaMcpError):
    pass


class InvalidChoice(KavitaMcpError, ValueError):
    pass


def _choice(name: str, value: str, allowed) -> str:
    if value not in allowed:
        raise InvalidChoice(f"{name} must be one of {', '.join(allowed)}; got {value!r}")
    return value


def _clamp(value: int, low: int, high: int) -> int:
    return max(low, min(int(value), high))


class Reader:
    def __init__(self, settings: Settings, kavita: KavitaClient):
        self._s = settings
        self._kavita = kavita
        self._sem = asyncio.Semaphore(CONCURRENCY)

    async def _author(self, series_id: int) -> str | None:
        async with self._sem:
            return author_of(await self._kavita.metadata(series_id))

    async def _rows(self, series_list: list[dict]) -> list[dict]:
        authors = await asyncio.gather(*(self._author(s["id"]) for s in series_list))
        return [series_row(s, author=a) for s, a in zip(series_list, authors, strict=True)]

    async def _resolve(self, kind: str, wanted: str, items: list[dict], key: str) -> int:
        target = wanted.strip().casefold()
        for item in items:
            if (item.get(key) or "").strip().casefold() == target:
                return item["id"]
        known = ", ".join(sorted(item.get(key) or "" for item in items)) or "none"
        raise UnknownName(f"No {kind} named {wanted!r}. Known: {known}")

    async def list_library(
        self,
        *,
        collection: str | None = None,
        library: str | None = None,
        unread_only: bool = False,
        sort: str = "added",
        page: int = 1,
        page_size: int = 25,
    ) -> dict:
        sort_field, ascending = SORTS[_choice("sort", sort, tuple(SORTS))]
        page = max(1, int(page))
        page_size = _clamp(page_size, 1, MAX_PAGE_SIZE)
        statements = []
        if collection:
            cid = await self._resolve(
                "collection", collection, await self._kavita.collections(), "title"
            )
            statements.append(
                {"field": FIELD_COLLECTION, "comparison": CONTAINS, "value": str(cid)}
            )
        if library:
            lid = await self._resolve("library", library, await self._kavita.libraries(), "name")
            statements.append({"field": FIELD_LIBRARY, "comparison": CONTAINS, "value": str(lid)})
        if unread_only:
            statements.append(UNREAD)
        result = await self._kavita.filter_series(
            statements, sort_field=sort_field, ascending=ascending, page=page, page_size=page_size
        )
        return {
            "items": await self._rows(result.items),
            "total": result.total,
            "page": page,
            "next_page": page + 1 if page * page_size < result.total else None,
        }

    async def search(self, query: str, limit: int = 10) -> dict:
        hits = (await self._kavita.search(query))[: _clamp(limit, 1, MAX_LIMIT)]

        async def full(series_id: int) -> dict:
            async with self._sem:
                return await self._kavita.series(series_id)

        series_list = await asyncio.gather(*(full(h["seriesId"]) for h in hits))
        return {"items": await self._rows(list(series_list))}

    async def get_document(self, series_id: int) -> dict:
        k = self._kavita
        series, metadata, detail, collections, wanted = await asyncio.gather(
            k.series(series_id),
            k.metadata(series_id),
            k.detail(series_id),
            k.collections_for_series(series_id),
            k.is_want_to_read(series_id),
        )
        chapters = (
            detail.get("specials", [])
            + detail.get("chapters", [])
            + [c for v in detail.get("volumes", []) for c in v.get("chapters", [])]
        )
        files = [f["filePath"] for c in chapters for f in c.get("files") or []]
        return {
            **series_row(series, author=author_of(metadata)),
            "summary": metadata.get("summary") or None,
            "collections": [c["title"] for c in collections],
            "file_path": files[0] if files else series.get("folderPath"),
            "want_to_read": bool(wanted),
            "url": series_url(self._s.kavita_public_url, series["libraryId"], series["id"]),
        }

    async def list_collections(self) -> dict:
        collections, libraries = await asyncio.gather(
            self._kavita.collections(), self._kavita.libraries()
        )
        return {
            "collections": sorted(c["title"] for c in collections),
            "libraries": sorted(lib["name"] for lib in libraries),
        }

    async def want_to_read(self, series_id: int, action: str) -> dict:
        add = _choice("action", action, ACTIONS) == "add"
        await self._kavita.set_want_to_read(series_id, add)
        return {"series_id": series_id, "want_to_read": add}

    async def reading_status(self, view: str = "on_deck", limit: int = 20) -> dict:
        _choice("view", view, VIEWS)
        limit = _clamp(limit, 1, MAX_PAGE_SIZE)
        if view == "on_deck":
            result = await self._kavita.on_deck(page_size=limit)
        elif view == "unread":
            result = await self._kavita.filter_series([UNREAD], page_size=limit)
        else:
            result = await self._kavita.want_to_read_list(page_size=limit)
        return {"view": view, "items": await self._rows(result.items), "total": result.total}

    async def mark_read(self, series_id: int) -> dict:
        await self._kavita.mark_read(series_id)
        return {"series_id": series_id, "read_status": "read"}
