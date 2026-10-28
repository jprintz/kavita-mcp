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
from datetime import UTC, date, datetime, timedelta

from kavita_mcp.config import Settings
from kavita_mcp.epub import KEEP, DocumentMeta, build_epub, read_info, rewrite_metadata
from kavita_mcp.errors import KavitaMcpError
from kavita_mcp.filebrowser import FileBrowserClient
from kavita_mcp.kavita import KavitaClient, KavitaError, KavitaForbidden, KavitaNotFound
from kavita_mcp.rows import series_url
from kavita_mcp.slug import InvalidTitle, locate, slug_from_kavita_folder, slugify

MAX_TITLE = 200
WATCHER_NOTE = "Kavita's folder watcher picks it up in about 6 minutes."
KEEP_FILE = ".kavita-mcp"
KEEP_TEXT = (
    b"kavita-mcp keeps this file so the Reports folder is never empty: Kavita skips\n"
    b"every scan of a library whose folder is empty.\n"
)
# A book Kavita scanned less than this long ago needs a forced re-read (see _reread).
RECENT_SCAN = timedelta(minutes=2)


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
        rescan_interval: float = 5.0,
    ):
        self._s = settings
        self._kavita = kavita
        self._files = files
        self._build = build
        self._today = today
        self._poll = poll_interval
        self._rescan = rescan_interval
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

    async def _scan(self, folder: str, *, present: bool) -> tuple[dict | None, str]:
        """Scan the Reports library now; wait until the folder's series is present/absent.

        Kavita errors become the note: the file change itself is already done.
        """
        if present:
            done, later = "The file is in place", WATCHER_NOTE
        else:
            done = "The book's folder is deleted"
            later = "Kavita's folder watcher drops it in about 6 minutes."
        try:
            library = await self.reports_library()
            await self._kavita.scan_library(library["id"])
        except KavitaForbidden:
            return None, f"{done}, but scan_now needs an admin Kavita key. {later}"
        except KavitaMcpError as e:
            return None, f"{done}, but Kavita could not scan now ({e}). {later}"
        deadline = time.monotonic() + self._s.scan_timeout_seconds
        requested = time.monotonic()
        while True:
            try:
                series = await self._kavita.series_by_folder(folder)
            except KavitaError as e:
                return None, f"{done}, but Kavita stopped answering ({e}). {later}"
            if present and series:
                return series, "Kavita has it now."
            if not present and not series:
                return None, "Kavita has removed it."
            if time.monotonic() >= deadline:
                timeout = f"{self._s.scan_timeout_seconds:.0f} s"
                return (series if not present else None), (
                    f"{done}, but Kavita hasn't caught up within {timeout}. {later}"
                )
            if time.monotonic() - requested >= self._rescan:
                # Kavita drops a scan request while a scan of the library is still
                # running ("duplicate request ... Skipping"), which may have missed
                # this file: ask again.
                requested = time.monotonic()
                try:
                    await self._kavita.scan_library(library["id"])
                except KavitaMcpError:
                    pass
            await asyncio.sleep(self._poll)

    async def _recently_scanned(self, series: dict) -> bool:
        try:
            last = datetime.fromisoformat(series.get("lastFolderScanned") or "")
        except ValueError:
            return False
        if last.tzinfo is None:
            last = last.replace(tzinfo=UTC)  # Kavita's local time: UTC in its image
        try:
            now = await self._kavita.server_time()
        except KavitaMcpError:
            now = datetime.now(UTC)
        return now - last < RECENT_SCAN

    async def _reread(self, series: dict, scan_now: bool) -> str:
        """After a book's file changed: have Kavita re-read it now when needed.

        Kavita 0.9.1 re-reads a book only when the file's mtime, cut to the minute,
        is later than its last scan (CacheHelper.IsFileUnmodifiedSinceCreationOrLastScan),
        so a change within the same minute is lost unless the scan is forced. Force
        it on scan_now, or when Kavita scanned the book less than two minutes ago;
        otherwise the folder watcher picks the change up.
        """
        if not scan_now and not await self._recently_scanned(series):
            return WATCHER_NOTE
        try:
            await self._kavita.scan_series(series["libraryId"], series["id"])
        except KavitaForbidden:
            return (
                "The file is in place, but re-reading it now needs an admin Kavita key. "
                "Kavita ignores a change made within a minute of its last scan of a book, "
                "so it may show the old version until its next full rescan."
            )
        except KavitaMcpError as e:
            return (
                f"The file is in place, but Kavita could not re-read it now ({e}). {WATCHER_NOTE}"
            )
        return "Kavita is re-reading it now; the change shows within seconds."

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
            series = None
            if existed:
                try:
                    series = await self._kavita.series_by_folder(loc.kavita_dir)
                except KavitaError:
                    series = None
            if series:
                note = await self._reread(series, scan_now)
            elif scan_now:
                series, note = await self._scan(loc.kavita_dir, present=True)
            else:
                note = WATCHER_NOTE
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
        series, loc = await self._managed(series_id)
        summary = summary if summary is KEEP else _clean(summary)
        collection = collection if collection is KEEP else _clean(collection)
        async with self._locks[loc.slug]:
            data = await self._files.download(loc.fb_file)
            old = read_info(data)
            new = rewrite_metadata(data, summary=summary, collection=collection)
            # Kavita first: if it fails, the EPUB still names the old collection,
            # so a retry removes the book from it again.
            if collection is not KEEP:
                if old.collection and (
                    collection is None or old.collection.casefold() != collection.casefold()
                ):
                    await self._kavita.remove_from_collection(series_id, old.collection)
                if collection:
                    await self._kavita.add_to_collection(series_id, collection)
            await self._files.upload(loc.fb_file, new)
        info = read_info(new)
        note = await self._reread(series, scan_now)
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
        reports_dir = f"{self._s.filebrowser_reports_dir}/"
        async with self._locks[loc.slug]:
            # Kavita aborts every scan of a library whose folder is empty: keep a
            # hidden file there.
            keep = reports_dir + KEEP_FILE
            if not await self._files.exists(keep):
                await self._files.upload(keep, KEEP_TEXT)
            await self._files.delete(loc.fb_dir)
            others = [n for n in await self._files.list_dir(reports_dir) if not n.startswith(".")]
        if not others:
            note = await self._delete_last(series_id)
        elif scan_now:
            _, note = await self._scan(loc.kavita_dir, present=False)
        else:
            note = "Kavita's folder watcher removes it in about 6 minutes."
        return {"series_id": series_id, "slug": loc.slug, "deleted": True, "note": note}

    async def _delete_last(self, series_id: int) -> str:
        """The last report: Kavita removes nothing in a scan that finds no series at
        all (0.9.1), so its series would stay. Its file is gone, so deleting the
        series in Kavita can't be undone by a scan."""
        try:
            await self._kavita.delete_series(series_id)
        except KavitaForbidden:
            return (
                "The book's folder is deleted, but removing the last report from Kavita needs "
                "an admin Kavita key; Kavita drops it when the next report is published."
            )
        except KavitaMcpError as e:
            return (
                f"The book's folder is deleted, but Kavita could not remove it ({e}); "
                "it drops it when the next report is published."
            )
        return "The book's folder is deleted and Kavita has removed it."

    async def health(self) -> dict:
        """Kavita answers, the Reports library exists, the upload service lists its folder."""
        kavita = await self._kavita.health()
        try:
            await self.reports_library()
            library = True
        except KavitaMcpError:
            library = False
        try:
            await self._files.list_dir(self._s.filebrowser_reports_dir + "/")
            upload = True
        except KavitaMcpError:
            upload = False
        return {"kavita": kavita, "library": library, "upload": upload}
