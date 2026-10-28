# kavita-mcp

An [MCP](https://modelcontextprotocol.io) server that publishes markdown as EPUB
books into a [Kavita](https://www.kavitareader.com) library, and reads your own
reading state there. Claude writes a report, calls `add_document`, and a few
minutes later the book is on your e-reader through Kavita's OPDS catalog.

- The EPUB is built with pandoc and carries everything Kavita needs: title,
  author, summary, language, date and collection. Kavita imports it with its
  folder watcher; nothing else has to happen.
- Files are written through [File Browser](https://filebrowser.org)'s HTTP API
  (the upload service next to Kavita), not a shared volume.
- Reading tools (on deck, unread, want-to-read, mark read) act as the user whose
  Kavita key the server has.
- No database. No book content is ever returned to the model.

Built for and tested against **Kavita 0.9.1** (0.9.1.4) and **File Browser
v2.63**.

## Tools

All ten carry `title`, `readOnlyHint`, `destructiveHint`, `idempotentHint` and
`openWorldHint` annotations.

| Tool | Parameters | What it does |
|---|---|---|
| `add_document` | `markdown`, `title`, `author`="Claude", `collection`, `summary`, `language`="en", `scan_now` | Builds the EPUB and writes it to `Reports/<slug>/<slug>.epub`. The same title replaces the same book (Kavita keeps its series, so reading progress survives). |
| `update_document` | `series_id`, `summary`, `collection`, `scan_now` | Rewrites a report's metadata in its EPUB. `collection` moves the book out of the collection it was published with; `""` clears a field. Reports only. |
| `delete_document` | `series_id`, `confirm`, `scan_now` | Deletes a report's folder. Needs `confirm: true`. Reports only. |
| `list_library` | `collection`, `library`, `unread_only`, `sort` (`added`/`title`/`read`), `page`, `page_size` (≤ 100) | Compact rows across all your libraries, with `total` and `next_page`. |
| `search` | `query`, `limit` | Title search (Kavita doesn't search summaries). |
| `get_document` | `series_id` | Author, summary, collections, word count, pages, progress, added date, file path, want-to-read, Kavita link. |
| `list_collections` | | Collection and library names, to reuse before publishing. |
| `want_to_read` | `series_id`, `action` (`add`/`remove`) | Your "read next" queue, which Kavita's OPDS catalog shows. |
| `reading_status` | `view` (`on_deck`/`unread`/`want_to_read`), `limit` | What you're reading, what you haven't started, your queue. |
| `mark_read` | `series_id` | Marks every page read. |

A row is `{series_id, title, author, library, added, word_count, pages,
read_status, progress_pct}`.

**Images:** only `data:` URIs (base64, up to `MAX_IMAGE_BYTES` each). Any other
image (a URL or a path) becomes its alt text; pandoc runs with `--sandbox`, so it
never fetches or reads anything else.

**`scan_now`:** asks Kavita to scan the Reports library now (seconds) instead of
waiting for its folder watcher (about 6 minutes). It needs an admin key; with
another key the file is still written and the answer says so.

## Configuration

| Variable | Default | |
|---|---|---|
| `KAVITA_API_KEY` | (required) | A Kavita auth key (user settings → Auth keys). The tools act as its user. |
| `KAVITA_URL` | `http://kavita:5000` | Kavita, as kavita-mcp reaches it |
| `KAVITA_PUBLIC_URL` | unset | Kavita's public URL, for links in answers |
| `KAVITA_REPORTS_PATH` | `/reports` | The Reports library's folder as Kavita sees it |
| `FILEBROWSER_URL` | `http://kavita-upload:8080` | File Browser, with its login method `noauth` |
| `FILEBROWSER_REPORTS_DIR` | `/Reports` | The same folder as File Browser sees it |
| `SCAN_TIMEOUT_SECONDS` | `60` | How long `scan_now` waits for Kavita |
| `MAX_MARKDOWN_BYTES` | `2097152` | Cap on `markdown` (including data URIs) |
| `MAX_IMAGE_BYTES` | `1048576` | Cap on each embedded image |
| `KAVITA_MCP_TRANSPORT` | `streamable-http` | or `stdio` |
| `KAVITA_MCP_HOST`, `KAVITA_MCP_PORT` | `0.0.0.0`, `8000` | The endpoint is `/mcp` |

`GET /healthz` answers `{"kavita": …, "library": …, "upload": …}` (503 if one is
false).

## Deployment

The image is `ghcr.io/jprintz/kavita-mcp:<version>` (amd64 and arm64). It needs a
writable `/tmp` (pandoc); everything else can be read-only.

The layout it expects: one folder that File Browser serves, with a `Books/` and a
`Reports/` folder inside, and Kavita mounting `Reports/` (read-only is enough) as
the folder of a **Book** library:

```
library/            File Browser's root
  Books/            Kavita: /books   (your other books)
  Reports/          Kavita: /reports (library "Reports", type Book)
```

A Podman Quadlet unit, as the author runs it next to Kavita and File Browser on a
network both share:

```ini
[Container]
ContainerName=kavita-mcp
Image=ghcr.io/jprintz/kavita-mcp:0.1.0
Network=edge.network
Secret=kavita_mcp_api_key,type=env,target=KAVITA_API_KEY
Environment=KAVITA_PUBLIC_URL=https://kavita.example.com
UserNS=auto
ReadOnly=true
NoNewPrivileges=true
DropCapability=ALL
```

In Kavita, once:

1. Server settings → turn on **folder watching** (it is off by default).
2. Add a library "Reports": type **Book**, folder `/reports`, folder watching on,
   **manage collections** on (collections come from the EPUB).

**Authentication:** the server has none of its own. Put it behind something that
authenticates every request: the author uses Cloudflare Access with Managed OAuth,
which is what claude.ai custom connectors expect. Don't expose it unauthenticated:
whoever reaches it acts with the Kavita key.

**Logs** carry the tool name, the slug or series ID, the outcome and the duration;
never the key, the markdown, summaries or Kavita's answers.

## Verified Kavita behaviour (0.9.1.4)

Checked against the source at the release commit and a live instance; the
integration tests pin most of it.

1. **EPUB metadata Kavita reads (Book library):** `dc:title` → series name,
   `dc:creator` → writer, `dc:description` → summary, `dc:language`, `dc:date` →
   release year, an EPUB3 collection title (`title-type` collection) → a collection
   owned by the server's first admin, reused by name (only with "manage collections"
   on). `dc:subject` becomes genres; nothing in an EPUB becomes Kavita tags. So no
   metadata API call is needed.
2. **Finding a new series:** `POST /api/Series/v2` with
   `{"field": 24, "comparison": 0, "value": "/reports/<slug>"}` (the series folder).
   `Library/scan-folder` waits a fixed minute and takes the key only in its body;
   `POST /api/Library/scan?libraryId=` starts right away, so `scan_now` uses that.
3. **Folder watching** is off by default (server setting). When on, a new or
   deleted folder shows up after about 6 minutes (5 minutes' debounce, then a scan
   1 minute later). It picks up writes from another container on the same host.
4. **Requests:** enums are integers; `Series/v2` returns the total only in the
   `Pagination` response header; collections: `Collection/update-for-series` with id
   0 always creates a new collection, so kavita-mcp looks one up by name first;
   `Collection/update-series` with `seriesIdsToRemove` removes.
5. **Permissions:** scanning (`Library/scan`, `Series/scan`, `scan-folder`),
   series metadata edits and series deletes are admin-only. Reading, search,
   want-to-read, progress and the user's own collections work with any key.
   Want-to-read, progress and collections are per user, so use the key of the
   person who reads.
6. **Auth:** the key goes in the `x-api-key` header on every call; no token
   exchange. A bad key gets 401 with an empty body.
7. **Overwriting an EPUB** keeps the series ID (same title) and updates it in
   place. Two catches, both handled:
   - Kavita re-reads a file only when its mtime, **cut to the minute**, is later
     than the book's last scan. A change within the same minute is ignored unless
     the scan is forced. kavita-mcp forces a re-read of that one book (`Series/scan`)
     on `scan_now`, and also without it when Kavita scanned the book less than two
     minutes ago.
   - Kavita drops a library-scan request while a scan of that library is still
     running, so `scan_now` repeats the request while it waits.

   A changed title makes a new series (progress and collections stay with the old
   one, which disappears), so `update_document` never changes titles.
8. **Collections** work without Kavita+. They are private to their owner unless
   promoted. A user's own collections show in their OPDS catalog.

Also found: Kavita cuts titles with a volume marker ("Report v2 Final" becomes
series "Report", volume 2) and merges every such book into one series. For those
titles the EPUB names its series explicitly (`calibre:series` = the title, with
Kavita's loose-leaf index), which keeps the title and a single book. Folders loose
in a library's root are ignored, hence one folder per book.

## Development

```bash
uv sync --all-extras
make test            # unit tests; set PANDOC (pandoc 3.11) and EPUBCHECK_JAR for the EPUB ones
make integration     # throwaway Kavita + File Browser + this image, then the acceptance tests
make throwaway-down
```

`scripts/throwaway.sh` starts Kavita 0.9.1 and File Browser laid out like the
deployment above, creates an admin, both libraries, a non-admin user and their
keys (in `.throwaway/env`, git-ignored). `scripts/record_fixtures.py` re-records the
Kavita responses the unit tests use. `uv run pytest -m watch` checks folder
watching on its own (about 7 minutes). In CI the integration workflow runs only when
started by hand.

## License

MIT
