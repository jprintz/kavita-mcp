"""Publishing reports: add, update and delete EPUBs in the Reports library.

The EPUB is the source of truth (title, author, summary, collection). Files go
through File Browser to /Reports/<slug>/<slug>.epub; Kavita sees them at
/reports/<slug>/ and imports them on its own (folder watching, about 6 minutes)
or right away with scan_now.
"""

import asyncio
import time
from collections import defaultdict
from collections.abc import Callable
from datetime import date

from kavita_mcp.config import Settings
from kavita_mcp.epub import KEEP, DocumentMeta, build_epub, read_info, rewrite_metadata
from kavita_mcp.errors import KavitaMcpError
from kavita_mcp.filebrowser import FileBrowserClient
from kavita_mcp.kavita import KavitaClient, KavitaError, KavitaForbidden, KavitaNotFound
from kavita_mcp.rows import series_url
from kavita_mcp.slug import InvalidTitle, locate, slug_from_kavita_folder, slugify

MAX_TITLE = 200
WATCHER_NOTE = "Kavita's folder watcher picks it up in about 6 minutes."


class NotAManagedReport(KavitaMcpError):
    pass


class ConfirmationRequired(KavitaMcpError):
    pass


class ReportsLibraryMissing(KavitaMcpError):
    pass


class NothingToChange(KavitaMcpError, ValueError):
    pass


def _clean(text: str | None) -> str | None:
    text = (text or "").strip()
    return text or None


class Publisher:
    def __init__(
        self,
        settings: Settings,
        kavita: KavitaClient,
        files: FileBrowserClient,
        *,
        build: Callable[..., bytes] = build_epub,
        today: Callable[[], date] = date.today,
        poll_interval: float = 1.0,
    ):
        self._s = settings
        self._kavita = kavita
        self._files = files
        self._build = build
        self._today = today
        self._poll = poll_interval
        self._library: dict | None = None
        self._locks: defaultdict[str, asyncio.Lock] = defaultdict(asyncio.Lock)

    def _locate(self, slug: str):
        return locate(
            slug, fb_root=self._s.filebrowser_reports_dir, kavita_root=self._s.kavita_reports_path
        )

    def _url(self, series: dict | None) -> str | None:
        if not series:
            return None
        return series_url(self._s.kavita_public_url, series["libraryId"], series["id"])

    async def reports_library(self) -> dict:
        if self._library is None:
            root = self._s.kavita_reports_path.rstrip("/")
            for lib in await self._kavita.libraries():
                if root in [f.rstrip("/") for f in lib.get("folders") or []]:
                    self._library = lib
                    break
            else:
                raise ReportsLibraryMissing(
                    f"No Kavita library has the folder {root}: add a Book library "
                    f'"Reports" with that folder (folder watching and manage collections on)'
                )
        return self._library

    async def _managed(self, series_id: int):
        try:
            series = await self._kavita.series(series_id)
        except KavitaNotFound:
            raise KavitaNotFound(f"Kavita has no series {series_id}") from None
        slug = slug_from_kavita_folder(series.get("folderPath") or "", self._s.kavita_reports_path)
        if slug is None:
            raise NotAManagedReport(
                f"Series {series_id} ({series.get('name')}) is not a report kavita-mcp published; "
                "only reports can be changed or deleted"
            )
        return series, self._locate(slug)

    async def _scan(self, folder: str, *, present: bool | None) -> tuple[dict | None, str]:
        """Scan the Reports library now; wait until the folder's series is present/absent.

        present=None: only start the scan. Kavita errors become the note: the file
        itself is already in place.
        """
        try:
            library = await self.reports_library()
            await self._kavita.scan_library(library["id"])
        except KavitaForbidden:
            return (
                None,
                f"The file is in place, but scan_now needs an admin Kavita key. {WATCHER_NOTE}",
            )
        except KavitaMcpError as e:
            return (
                None,
                f"The file is in place, but Kavita could not scan it now ({e}). {WATCHER_NOTE}",
            )
        if present is None:
            return None, "Kavita is rescanning it now; the change shows within seconds."
        deadline = time.monotonic() + self._s.scan_timeout_seconds
        while True:
            try:
                series = await self._kavita.series_by_folder(folder)
            except KavitaError as e:
                return (
                    None,
                    f"The file is in place, but Kavita stopped answering ({e}). {WATCHER_NOTE}",
                )
            if present and series:
                return series, "Kavita has it now."
            if not present and not series:
                return None, "Kavita has removed it."
            if time.monotonic() >= deadline:
                timeout = f"{self._s.scan_timeout_seconds:.0f} s"
                if present:
                    return None, (
                        f"The file is in place, but Kavita hasn't listed it within {timeout}; "
                        "it will on its next scan."
                    )
                return series, f"The folder is gone, but Kavita still lists it after {timeout}."
            await asyncio.sleep(self._poll)

    async def add(
        self,
        *,
        markdown: str,
        title: str,
        author: str = "Claude",
        collection: str | None = None,
        summary: str | None = None,
        language: str = "en",
        scan_now: bool = False,
    ) -> dict:
        title = title.strip()
        if not title or len(title) > MAX_TITLE:
            raise InvalidTitle(f"The title must be 1 to {MAX_TITLE} characters")
        loc = self._locate(slugify(title))
        meta = DocumentMeta(
            title=title,
            author=_clean(author) or "Claude",
            language=_clean(language) or "en",
            date=self._today().isoformat(),
            summary=_clean(summary),
            collection=_clean(collection),
            slug=loc.slug,
        )
        async with self._locks[loc.slug]:
            existed = await self._files.exists(loc.fb_file)
            epub = await asyncio.to_thread(
                self._build,
                markdown,
                meta,
                max_markdown_bytes=self._s.max_markdown_bytes,
                max_image_bytes=self._s.max_image_bytes,
            )
            await self._files.upload(loc.fb_file, epub)
            if scan_now:
                series, note = await self._scan(loc.kavita_dir, present=True)
            else:
                series, note = None, WATCHER_NOTE
                if existed:
                    try:
                        series = await self._kavita.series_by_folder(loc.kavita_dir)
                    except KavitaError:
                        series = None
        return {
            "status": "updated" if existed else "published",
            "slug": loc.slug,
            "path": f"{loc.kavita_dir}/{loc.slug}.epub",
            "series_id": series["id"] if series else None,
            "url": self._url(series),
            "collection": meta.collection,
            "note": note,
        }

    async def update(
        self, series_id: int, *, summary=KEEP, collection=KEEP, scan_now: bool = False
    ) -> dict:
        if summary is KEEP and collection is KEEP:
            raise NothingToChange("Give a summary or a collection to change")
        _, loc = await self._managed(series_id)
        summary = summary if summary is KEEP else _clean(summary)
        collection = collection if collection is KEEP else _clean(collection)
        async with self._locks[loc.slug]:
            data = await self._files.download(loc.fb_file)
            old = read_info(data)
            new = rewrite_metadata(data, summary=summary, collection=collection)
            await self._files.upload(loc.fb_file, new)
            if collection is not KEEP:
                if old.collection and (
                    collection is None or old.collection.casefold() != collection.casefold()
                ):
                    await self._kavita.remove_from_collection(series_id, old.collection)
                if collection:
                    await self._kavita.add_to_collection(series_id, collection)
        info = read_info(new)
        if scan_now:
            _, note = await self._scan(loc.kavita_dir, present=None)
        else:
            note = WATCHER_NOTE
        return {
            "series_id": series_id,
            "slug": loc.slug,
            "summary": info.summary,
            "collection": info.collection,
            "note": note,
        }

    async def delete(self, series_id: int, *, confirm: bool, scan_now: bool = False) -> dict:
        if confirm is not True:
            raise ConfirmationRequired(
                "delete_document deletes the book: call it with confirm: true"
            )
        _, loc = await self._managed(series_id)
        async with self._locks[loc.slug]:
            await self._files.delete(loc.fb_dir)
        if scan_now:
            _, note = await self._scan(loc.kavita_dir, present=False)
        else:
            note = "Kavita's folder watcher removes it in about 6 minutes."
        return {"series_id": series_id, "slug": loc.slug, "deleted": True, "note": note}
