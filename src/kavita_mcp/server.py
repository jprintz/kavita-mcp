"""The MCP server: ten tools, a /healthz route, and the entry point."""

import argparse
import logging
import time
from collections.abc import Awaitable, Callable
from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as package_version
from typing import Annotated, Any, Literal

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ToolAnnotations
from pydantic import Field
from starlette.requests import Request
from starlette.responses import JSONResponse

from kavita_mcp.config import Settings
from kavita_mcp.documents import Publisher
from kavita_mcp.epub import KEEP
from kavita_mcp.errors import KavitaMcpError
from kavita_mcp.filebrowser import FileBrowserClient
from kavita_mcp.kavita import KavitaClient
from kavita_mcp.reading import Reader

log = logging.getLogger("kavita_mcp")

try:
    __version__ = package_version("kavita-mcp")
except PackageNotFoundError:  # running from a source checkout
    __version__ = "0.0.0"

INSTRUCTIONS = (
    "Publishes markdown reports as EPUB books into the user's Kavita library (library "
    '"Reports"), where they reach their e-reader through OPDS, and reads the user\'s own '
    "reading state across all their libraries: what they are in the middle of, what is "
    "unread, their want-to-read queue. A new report appears in Kavita about 6 minutes after "
    "add_document, or within seconds with scan_now. Call list_collections before inventing a "
    "collection name. There are no tags. Book contents are never returned."
)


def _hints(title: str, *, read_only: bool, destructive: bool) -> ToolAnnotations:
    return ToolAnnotations(
        title=title,
        read_only_hint=read_only,
        destructive_hint=destructive,
        idempotent_hint=True,
        open_world_hint=False,
    )


READ = {"read_only": True, "destructive": False}
WRITE = {"read_only": False, "destructive": True}

SeriesId = Annotated[
    int, Field(description="Kavita series ID (from list_library, search or add_document)")
]
ScanNow = Annotated[
    bool,
    Field(
        description="Ask Kavita to scan the Reports library now (seconds) instead of waiting "
        "for its folder watcher (about 6 minutes). Needs an admin Kavita key."
    ),
]


async def _run(name: str, target: Any, call: Callable[[], Awaitable[dict]]) -> dict:
    """Log tool name, target and outcome (never arguments); errors become ToolError."""
    start = time.monotonic()
    outcome = "ok"
    try:
        result = await call()
        if target is None and isinstance(result, dict):
            target = result.get("slug")
        return result
    except KavitaMcpError as e:
        outcome = f"error:{type(e).__name__}"
        raise ToolError(str(e)) from None
    except Exception as e:
        outcome = f"error:{type(e).__name__}"
        raise
    finally:
        ms = round((time.monotonic() - start) * 1000)
        log.info("tool=%s target=%s outcome=%s ms=%d", name, target or "-", outcome, ms)


def create_server(publisher: Publisher, reader: Reader) -> MCPServer:
    mcp = MCPServer("kavita_mcp", version=__version__, instructions=INSTRUCTIONS)

    @mcp.tool(annotations=_hints("Publish a markdown document as a book", **WRITE))
    async def add_document(
        markdown: Annotated[
            str,
            Field(description="The document, in markdown. Headings become the table of contents."),
        ],
        title: Annotated[
            str,
            Field(
                description="Book title, up to 200 characters. The same title replaces the same book."
            ),
        ],
        author: Annotated[
            str, Field(description="Author shown in Kavita and on the e-reader")
        ] = "Claude",
        collection: Annotated[
            str | None,
            Field(
                description="Kavita collection to put the book in (reused by name, created if new)"
            ),
        ] = None,
        summary: Annotated[
            str | None, Field(description="Short description shown in Kavita")
        ] = None,
        language: Annotated[str, Field(description="Language code, e.g. en or sv")] = "en",
        scan_now: ScanNow = False,
    ) -> dict:
        """Build an EPUB from markdown and put it in the Kavita library "Reports".

        Publishing a title that already exists replaces that book (same folder, same
        Kavita series, reading progress kept); a new title is a new book. Images only
        as data: URIs (base64, up to 1 MiB each); any other image (a URL or a path)
        becomes its alt text. No tags. Returns the series ID when Kavita already has
        the book or scan_now found it; otherwise Kavita lists it in about 6 minutes.
        To move a book to another collection, use update_document.
        """
        return await _run(
            "add_document",
            None,
            lambda: publisher.add(
                markdown=markdown,
                title=title,
                author=author,
                collection=collection,
                summary=summary,
                language=language,
                scan_now=scan_now,
            ),
        )

    @mcp.tool(annotations=_hints("Change a published report's summary or collection", **WRITE))
    async def update_document(
        series_id: SeriesId,
        summary: Annotated[
            str | None, Field(description='New summary; "" removes it; leave out to keep it')
        ] = None,
        collection: Annotated[
            str | None,
            Field(
                description='Move the book to this collection; "" takes it out of its collection; leave out to keep it'
            ),
        ] = None,
        scan_now: ScanNow = False,
    ) -> dict:
        """Change the summary or the collection of a report published with add_document.

        Works only on reports (the "Reports" library), and needs the Kavita series ID:
        for a report Kavita hasn't listed yet, call add_document again with the same
        title instead. collection moves the book: it leaves the collection it was
        published with and joins the new one; collections added by hand stay.
        """
        return await _run(
            "update_document",
            series_id,
            lambda: publisher.update(
                series_id,
                summary=KEEP if summary is None else summary,
                collection=KEEP if collection is None else collection,
                scan_now=scan_now,
            ),
        )

    @mcp.tool(annotations=_hints("Delete a published report", **WRITE))
    async def delete_document(
        series_id: SeriesId,
        confirm: Annotated[bool, Field(description="Must be true: deleting removes the book file")],
        scan_now: ScanNow = False,
    ) -> dict:
        """Delete a report published with add_document: its file and, after Kavita's next
        scan, its Kavita entry. Only reports can be deleted, never books from other libraries."""
        return await _run(
            "delete_document",
            series_id,
            lambda: publisher.delete(series_id, confirm=confirm, scan_now=scan_now),
        )

    @mcp.tool(annotations=_hints("List books in the library", **READ))
    async def list_library(
        collection: Annotated[
            str | None, Field(description="Only books in this collection")
        ] = None,
        library: Annotated[
            str | None, Field(description='Only this Kavita library, e.g. "Reports" or "Books"')
        ] = None,
        unread_only: Annotated[bool, Field(description="Only books not started")] = False,
        sort: Annotated[
            Literal["added", "title", "read"],
            Field(description="added: newest first; title: A-Z; read: last read first"),
        ] = "added",
        page: Annotated[int, Field(ge=1)] = 1,
        page_size: Annotated[int, Field(ge=1, le=100)] = 25,
    ) -> dict:
        """List the user's books across all their Kavita libraries, one compact row each
        (series ID, title, author, library, added, word count, pages, read status,
        progress). Returns total and next_page."""
        return await _run(
            "list_library",
            collection or library,
            lambda: reader.list_library(
                collection=collection,
                library=library,
                unread_only=unread_only,
                sort=sort,
                page=page,
                page_size=page_size,
            ),
        )

    @mcp.tool(annotations=_hints("Search books by name", **READ))
    async def search(
        query: Annotated[str, Field(description="Words from a title (summaries are not searched)")],
        limit: Annotated[int, Field(ge=1, le=50)] = 10,
    ) -> dict:
        """Find books whose title matches, with the same rows as list_library."""
        return await _run("search", None, lambda: reader.search(query, limit))

    @mcp.tool(annotations=_hints("Show one book's details", **READ))
    async def get_document(series_id: SeriesId) -> dict:
        """Details of one book: author, summary, collections, word count, pages, reading
        progress, added date, file path, want-to-read, Kavita link. Never the content."""
        return await _run("get_document", series_id, lambda: reader.get_document(series_id))

    @mcp.tool(annotations=_hints("List collections and libraries", **READ))
    async def list_collections() -> dict:
        """The user's collection names and library names, to reuse instead of inventing
        near-duplicates before add_document."""
        return await _run("list_collections", None, reader.list_collections)

    @mcp.tool(annotations=_hints("Add to or remove from want-to-read", **WRITE))
    async def want_to_read(
        series_id: SeriesId,
        action: Annotated[Literal["add", "remove"], Field(description="add or remove")] = "add",
    ) -> dict:
        """The user's "read next" queue, shown in Kavita and over OPDS on the e-reader."""
        return await _run("want_to_read", series_id, lambda: reader.want_to_read(series_id, action))

    @mcp.tool(annotations=_hints("What the user is reading or hasn't read", **READ))
    async def reading_status(
        view: Annotated[
            Literal["on_deck", "unread", "want_to_read"],
            Field(description="on_deck: in progress; unread: not started; want_to_read: the queue"),
        ] = "on_deck",
        limit: Annotated[int, Field(ge=1, le=100)] = 20,
    ) -> dict:
        """Answers "what am I in the middle of?", "what haven't I read?" and "what's next?"."""
        return await _run("reading_status", view, lambda: reader.reading_status(view, limit))

    @mcp.tool(annotations=_hints("Mark a book as read", **WRITE))
    async def mark_read(series_id: SeriesId) -> dict:
        """Mark every page of the book as read (replaces the current position)."""
        return await _run("mark_read", series_id, lambda: reader.mark_read(series_id))

    @mcp.custom_route("/healthz", methods=["GET"])
    async def healthz(request: Request) -> JSONResponse:
        checks = await publisher.health()
        return JSONResponse(checks, status_code=200 if all(checks.values()) else 503)

    return mcp


def main() -> None:
    """Run kavita-mcp: Streamable HTTP on /mcp (default), or stdio for local testing."""
    settings = Settings.from_env()
    parser = argparse.ArgumentParser(prog="kavita-mcp", description=main.__doc__)
    parser.add_argument(
        "--transport", choices=["stdio", "streamable-http"], default=settings.transport
    )
    parser.add_argument("--host", default=settings.host)
    parser.add_argument("--port", type=int, default=settings.port)
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")

    kavita = KavitaClient(settings.kavita_url, settings.kavita_api_key)
    files = FileBrowserClient(settings.filebrowser_url)
    server = create_server(Publisher(settings, kavita, files), Reader(settings, kavita))
    if args.transport == "streamable-http":
        # Bound to 0.0.0.0 behind the tunnel, the Host header is the public name, so
        # the SDK's DNS-rebinding check (on only for localhost) stays off.
        server.run("streamable-http", host=args.host, port=args.port, stateless_http=True)
    else:
        server.run("stdio")


if __name__ == "__main__":
    main()
