import json

import httpx
import pytest

from kavita_mcp.kavita import (
    KavitaAuthError,
    KavitaBadRequest,
    KavitaClient,
    KavitaForbidden,
    KavitaNotFound,
    KavitaUnreachable,
)
from tests.fixtures import FixtureTransport, load

KEY = "testkey0123456789testkey01234567"
ROUTES = {
    ("GET", "/api/Library/libraries"): "libraries",
    ("GET", "/api/Series/3"): "series",
    ("GET", "/api/Series/metadata"): "metadata",
    ("GET", "/api/Series/series-detail"): "series_detail",
    ("POST", "/api/Series/v2"): "series_v2_page1",
    ("GET", "/api/Search/search"): "search",
    ("POST", "/api/Series/on-deck"): "on_deck",
    ("POST", "/api/want-to-read/v2"): "want_to_read_v2",
    ("GET", "/api/Collection"): "collections",
    ("GET", "/api/Collection/all-series"): "collections_for_series",
    ("POST", "/api/Collection/update-for-series"): lambda r: httpx.Response(200),
    ("POST", "/api/Collection/update-series"): lambda r: httpx.Response(200),
    ("POST", "/api/Library/scan"): lambda r: httpx.Response(200),
    ("GET", "/api/health"): lambda r: httpx.Response(200),
}


def make(routes=None) -> tuple[KavitaClient, FixtureTransport]:
    transport = FixtureTransport(ROUTES | (routes or {}))
    http = httpx.AsyncClient(transport=transport, base_url="http://kavita:5000")
    return KavitaClient("http://kavita:5000", KEY, http=http), transport


async def test_sends_api_key_header():
    client, transport = make()
    await client.libraries()
    await client.series(3)
    assert {r.headers["x-api-key"] for r in transport.requests} == {KEY}


async def test_reads_series_fields():
    client, _ = make()
    series = await client.series(3)
    assert series["name"] == "Weekly Digest"
    assert series["folderPath"] == "/reports/weekly-digest"


async def test_filter_series_total_from_pagination_header():
    client, _ = make()
    page = await client.filter_series([], page=1, page_size=2)
    assert page.total == 3
    assert len(page.items) == 2


async def test_filter_series_request():
    client, transport = make()
    await client.filter_series([{"field": 1, "comparison": 7, "value": "x"}], page=2, page_size=5)
    request = transport.last("POST", "/api/Series/v2")
    assert json.loads(request.content) == {
        "statements": [{"field": 1, "comparison": 7, "value": "x"}],
        "combination": 1,
        "sortOptions": {"sortField": 2, "isAscending": False},
        "limitTo": 0,
    }
    assert request.url.params["PageNumber"] == "2"
    assert request.url.params["PageSize"] == "5"


async def test_series_by_folder_statement():
    client, transport = make()
    found = await client.series_by_folder("/reports/q3")
    assert json.loads(transport.last("POST", "/api/Series/v2").content)["statements"] == [
        {"field": 24, "comparison": 0, "value": "/reports/q3"}
    ]
    assert found["id"] == load("series_v2_page1")["body"][0]["id"]


async def test_series_by_folder_none_when_empty():
    empty = {("POST", "/api/Series/v2"): lambda r: httpx.Response(200, json=[])}
    client, _ = make(empty)
    assert await client.series_by_folder("/reports/nothing") is None


async def test_search_returns_series_hits():
    client, transport = make()
    hits = await client.search("weekly")
    assert [h["name"] for h in hits] == ["Weekly Digest"]
    assert transport.last("GET", "/api/Search/search").url.params["queryString"] == "weekly"


async def test_on_deck_and_want_to_read_pages():
    client, _ = make()
    assert (await client.on_deck(page_size=20)).total == 1
    assert (await client.want_to_read_list(page_size=20)).total == 1


@pytest.mark.parametrize(
    "status,exc",
    [
        (401, KavitaAuthError),
        (403, KavitaForbidden),
        (404, KavitaNotFound),
        (400, KavitaBadRequest),
    ],
)
async def test_status_errors(status, exc):
    client, _ = make({("GET", "/api/Series/3"): lambda r: httpx.Response(status, text="nope")})
    with pytest.raises(exc):
        await client.series(3)


async def test_auth_error_names_the_setting():
    client, _ = make({("GET", "/api/Series/3"): lambda r: httpx.Response(401)})
    with pytest.raises(KavitaAuthError, match="KAVITA_API_KEY"):
        await client.series(3)


async def test_unreachable():
    def refuse(request):
        raise httpx.ConnectError("refused", request=request)

    client, _ = make({("GET", "/api/Series/3"): refuse})
    with pytest.raises(KavitaUnreachable):
        await client.series(3)


async def test_add_to_collection_reuses_case_insensitive():
    client, transport = make()
    collection_id = await client.add_to_collection(7, "weekly")
    assert collection_id == 1
    assert transport.bodies("POST", "/api/Collection/update-for-series") == [
        {"collectionTagId": 1, "collectionTagTitle": "Weekly", "seriesIds": [7]}
    ]
    assert transport.last("GET", "/api/Collection").url.params["ownedOnly"] == "true"


async def test_add_to_collection_creates():
    calls = {"n": 0}
    created = {"id": 9, "title": "Research", "owner": "kadmin"}

    def collections(request):
        calls["n"] += 1
        body = load("collections")["body"] + ([created] if calls["n"] > 1 else [])
        return httpx.Response(200, json=body)

    client, transport = make({("GET", "/api/Collection"): collections})
    assert await client.add_to_collection(7, "Research") == 9
    assert transport.bodies("POST", "/api/Collection/update-for-series") == [
        {"collectionTagId": 0, "collectionTagTitle": "Research", "seriesIds": [7]}
    ]


async def test_remove_from_collection_body():
    client, transport = make()
    await client.remove_from_collection(3, "WEEKLY")
    assert transport.bodies("POST", "/api/Collection/update-series") == [
        {"tag": {"id": 1, "title": "Weekly"}, "seriesIdsToRemove": [3]}
    ]


async def test_remove_from_missing_collection_is_noop():
    client, transport = make()
    await client.remove_from_collection(3, "No such")
    assert transport.bodies("POST", "/api/Collection/update-series") == []


async def test_scan_library():
    client, transport = make()
    await client.scan_library(2)
    assert transport.last("POST", "/api/Library/scan").url.params["libraryId"] == "2"


async def test_health():
    client, _ = make()
    assert await client.health() is True
    down, _ = make({("GET", "/api/health"): lambda r: httpx.Response(503)})
    assert await down.health() is False
