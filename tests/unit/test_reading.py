import json

import httpx
import pytest

from kavita_mcp.config import Settings
from kavita_mcp.kavita import KavitaClient
from kavita_mcp.reading import Reader, UnknownName
from tests.fixtures import FixtureTransport
from tests.unit.test_kavita import ROUTES

SETTINGS = Settings.from_env({"KAVITA_API_KEY": "k" * 20, "KAVITA_PUBLIC_URL": "https://k.example"})
ROW_KEYS = {
    "series_id",
    "title",
    "author",
    "library",
    "added",
    "word_count",
    "pages",
    "read_status",
    "progress_pct",
}
EXTRA = {
    ("GET", "/api/want-to-read"): lambda r: httpx.Response(200, json=True),
    ("POST", "/api/want-to-read/add-series"): lambda r: httpx.Response(200),
    ("POST", "/api/want-to-read/remove-series"): lambda r: httpx.Response(200),
    ("POST", "/api/Reader/mark-read"): lambda r: httpx.Response(200),
}


def make():
    transport = FixtureTransport(ROUTES | EXTRA)
    http = httpx.AsyncClient(transport=transport)
    return Reader(SETTINGS, KavitaClient("http://kavita:5000", "key", http=http)), transport


def statements(transport):
    return json.loads(transport.last("POST", "/api/Series/v2").content)["statements"]


async def test_list_library_rows_and_paging():
    reader, transport = make()
    result = await reader.list_library(page_size=2)
    assert len(result["items"]) == 2
    assert result["total"] == 3
    assert result["next_page"] == 2
    assert all(set(row) == ROW_KEYS for row in result["items"])
    assert result["items"][0]["author"] == "Claude"
    sort = json.loads(transport.last("POST", "/api/Series/v2").content)["sortOptions"]
    assert sort == {"sortField": 2, "isAscending": False}


async def test_last_page_has_no_next():
    reader, _ = make()
    assert (await reader.list_library(page=2, page_size=2))["next_page"] is None


async def test_list_library_collection_filter():
    reader, transport = make()
    await reader.list_library(collection="weekly")
    assert statements(transport) == [{"field": 7, "comparison": 5, "value": "1"}]


async def test_unknown_collection_lists_known():
    reader, _ = make()
    with pytest.raises(UnknownName, match="Weekly"):
        await reader.list_library(collection="Nope")


async def test_library_filter():
    reader, transport = make()
    await reader.list_library(library="reports")
    assert statements(transport) == [{"field": 19, "comparison": 5, "value": "2"}]


async def test_unknown_library_lists_known():
    reader, _ = make()
    with pytest.raises(UnknownName, match="Books"):
        await reader.list_library(library="Comics")


async def test_unread_only_statement():
    reader, transport = make()
    await reader.list_library(unread_only=True)
    assert statements(transport) == [{"field": 20, "comparison": 0, "value": "0"}]


@pytest.mark.parametrize("sort,expected", [("title", (1, True)), ("read", (7, False))])
async def test_sort_options(sort, expected):
    reader, transport = make()
    await reader.list_library(sort=sort)
    options = json.loads(transport.last("POST", "/api/Series/v2").content)["sortOptions"]
    assert (options["sortField"], options["isAscending"]) == expected


async def test_page_size_capped():
    reader, transport = make()
    await reader.list_library(page_size=500)
    assert transport.last("POST", "/api/Series/v2").url.params["PageSize"] == "100"


async def test_search_rows():
    reader, _ = make()
    result = await reader.search("weekly")
    assert [(r["series_id"], r["title"]) for r in result["items"]] == [(3, "Weekly Digest")]
    assert set(result["items"][0]) == ROW_KEYS


async def test_get_document():
    reader, _ = make()
    doc = await reader.get_document(3)
    assert set(doc) == ROW_KEYS | {"summary", "collections", "file_path", "want_to_read", "url"}
    assert doc["collections"] == ["Weekly"]
    assert doc["file_path"] == "/reports/weekly-digest/weekly-digest.epub"
    assert doc["want_to_read"] is True
    assert doc["summary"] == "What happened this week."
    assert doc["url"] == "https://k.example/library/2/series/3"


async def test_get_document_has_no_content():
    reader, _ = make()
    doc = await reader.get_document(3)
    assert all(len(str(v)) <= 2000 for v in doc.values())


async def test_list_collections():
    reader, _ = make()
    assert await reader.list_collections() == {
        "collections": ["Weekly"],
        "libraries": ["Books", "Reports"],
    }


async def test_reading_status_views():
    reader, transport = make()
    on_deck = await reader.reading_status("on_deck")
    assert [(r["title"], r["read_status"]) for r in on_deck["items"]] == [
        ("Market Report", "in_progress")
    ]
    await reader.reading_status("unread", limit=5)
    assert statements(transport) == [{"field": 20, "comparison": 0, "value": "0"}]
    assert transport.last("POST", "/api/Series/v2").url.params["PageSize"] == "5"
    queue = await reader.reading_status("want_to_read")
    assert [r["title"] for r in queue["items"]] == ["Weekly Digest"]


async def test_want_to_read_and_mark_read():
    reader, transport = make()
    assert await reader.want_to_read(3, "add") == {"series_id": 3, "want_to_read": True}
    assert transport.bodies("POST", "/api/want-to-read/add-series") == [{"seriesIds": [3]}]
    assert await reader.want_to_read(3, "remove") == {"series_id": 3, "want_to_read": False}
    assert await reader.mark_read(3) == {"series_id": 3, "read_status": "read"}
    assert transport.bodies("POST", "/api/Reader/mark-read") == [{"seriesId": 3}]


@pytest.mark.parametrize(
    "call",
    [
        lambda r: r.reading_status("later"),
        lambda r: r.want_to_read(3, "toggle"),
        lambda r: r.list_library(sort="random"),
    ],
)
async def test_invalid_choices_name_the_allowed_values(call):
    reader, _ = make()
    with pytest.raises(ValueError, match="one of"):
        await call(reader)
