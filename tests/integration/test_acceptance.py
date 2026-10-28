"""The handoff's acceptance tests 1-9, against the throwaway (see conftest.py)."""

import pytest
from mcp import Client

from tests.conftest import FIXTURES
from tests.integration.conftest import call, docker_exec, eventually

pytestmark = pytest.mark.integration


async def series_by_folder(kavita, folder: str) -> list[dict]:
    r = await kavita.post(
        "/api/Series/v2",
        json={"statements": [{"field": 24, "comparison": 0, "value": folder}], "combination": 1},
    )
    r.raise_for_status()
    return r.json()


async def publish(env, title: str, **kwargs) -> dict:
    args = {"markdown": f"# {title}\n\nSome text.\n", "title": title, "scan_now": True} | kwargs
    error, result = await call(env["KAVITA_MCP_URL"], "add_document", args)
    assert not error, result
    if args.get("scan_now"):
        assert result["series_id"], result
    return result


async def test_health_route(env):
    import httpx

    url = env["KAVITA_MCP_URL"].removesuffix("/mcp") + "/healthz"
    async with httpx.AsyncClient() as http:
        r = await http.get(url)
    assert r.json() == {"kavita": True, "library": True, "upload": True}


async def test_add_scan_now_lands_in_reports(env, kavita, unique):
    collection = f"Weekly {unique}"
    result = await publish(
        env, f"Acceptance one {unique}", collection=collection, summary="Sum one", author="Ada"
    )
    sid = result["series_id"]
    assert sid and result["status"] == "published"
    series = (await kavita.get(f"/api/Series/{sid}")).json()
    assert series["libraryId"] == int(env["REPORTS_LIBRARY_ID"])
    meta = (await kavita.get("/api/Series/metadata", params={"seriesId": sid})).json()
    assert meta["summary"] == "Sum one"
    assert [w["name"] for w in meta["writers"]] == ["Ada"]

    async def in_collection():
        r = await kavita.get("/api/Collection/all-series", params={"seriesId": sid})
        return collection in [c["title"] for c in r.json()]

    assert await eventually(in_collection)


async def test_listed_in_opds(env, kavita, unique):
    title = f"Opds book {unique}"
    await publish(env, title)
    r = await kavita.get(f"/api/opds/{env['KAVITA_API_KEY']}/libraries/{env['REPORTS_LIBRARY_ID']}")
    assert r.status_code == 200
    assert title in r.text


async def test_republish_same_title_no_duplicate(env, kavita, unique):
    title = f"Twice {unique}"
    first = await publish(env, title)
    second = await publish(env, title, markdown="# Changed\n\nNew text.\n")
    assert second["status"] == "updated"
    assert second["series_id"] == first["series_id"]
    assert len(await series_by_folder(kavita, f"/reports/twice-{unique}")) == 1


async def test_update_moves_collection_and_summary(env, kavita, unique):
    col_a, col_b = f"Col A {unique}", f"Col B {unique}"
    sid = (await publish(env, f"Mover {unique}", collection=col_a))["series_id"]

    async def collections():
        r = await kavita.get("/api/Collection/all-series", params={"seriesId": sid})
        return sorted(c["title"] for c in r.json())

    assert await eventually(lambda: _eq(collections, [col_a]))
    error, result = await call(
        env["KAVITA_MCP_URL"],
        "update_document",
        {"series_id": sid, "collection": col_b, "summary": "New sum", "scan_now": True},
    )
    assert not error, result
    assert result["collection"] == col_b
    assert await eventually(lambda: _eq(collections, [col_b]))

    async def summary():
        r = await kavita.get("/api/Series/metadata", params={"seriesId": sid})
        return r.json()["summary"] == "New sum"

    assert await eventually(summary)


async def _eq(get, expected):
    return await get() == expected


async def test_list_library_paging_and_filters(env, unique):
    collection = f"Paging {unique}"
    for n in range(3):
        await publish(env, f"Page book {n} {unique}", collection=collection)
    url = env["KAVITA_MCP_URL"]

    async def first_page():
        error, result = await call(url, "list_library", {"collection": collection, "page_size": 2})
        return None if error or result["total"] < 3 else result

    page1 = await eventually(first_page)
    assert page1 and len(page1["items"]) == 2 and page1["next_page"] == 2
    error, page2 = await call(
        url, "list_library", {"collection": collection, "page_size": 2, "page": 2}
    )
    assert not error and len(page2["items"]) == 1 and page2["next_page"] is None
    error, reports = await call(url, "list_library", {"library": "reports", "page_size": 100})
    assert not error and {row["library"] for row in reports["items"]} == {"Reports"}


async def test_search_by_title_word(env, unique):
    await publish(env, f"Zebra quarterly {unique}")
    error, result = await call(
        env["KAVITA_MCP_URL"], "search", {"query": f"zebra quarterly {unique}"}
    )
    assert not error
    assert [r["title"] for r in result["items"]] == [f"Zebra quarterly {unique}"]


async def test_delete_confirm_and_no_comeback(env, kavita, unique):
    sid = (await publish(env, f"Doomed {unique}"))["series_id"]
    url = env["KAVITA_MCP_URL"]
    error, text = await call(url, "delete_document", {"series_id": sid, "confirm": False})
    assert error and "confirm" in text
    error, result = await call(
        url, "delete_document", {"series_id": sid, "confirm": True, "scan_now": True}
    )
    assert not error and result["deleted"] is True
    assert await series_by_folder(kavita, f"/reports/doomed-{unique}") == []
    await kavita.post("/api/Library/scan", params={"libraryId": env["REPORTS_LIBRARY_ID"]})

    async def still_gone():
        return await series_by_folder(kavita, f"/reports/doomed-{unique}") == []

    assert await eventually(still_gone, timeout=5)
    assert (await kavita.get(f"/api/Series/{sid}")).status_code == 404


async def test_books_series_refused(env, kavita, unique):
    import httpx

    async with httpx.AsyncClient(base_url=env["FILEBROWSER_URL"]) as fb:
        token = (await fb.post("/api/login", content=b"{}")).text
        r = await fb.post(
            f"/api/resources/Books/Sample {unique}/sample.epub",
            params={"override": "true"},
            headers={"X-Auth": token},
            content=(FIXTURES / "sample.epub").read_bytes(),
        )
        r.raise_for_status()
    await kavita.post("/api/Library/scan", params={"libraryId": env["BOOKS_LIBRARY_ID"]})

    async def book():
        found = await series_by_folder(kavita, f"/books/Sample {unique}")
        return found[0]["id"] if found else None

    sid = await eventually(book)
    assert sid
    for tool, args in (
        ("delete_document", {"series_id": sid, "confirm": True}),
        ("update_document", {"series_id": sid, "summary": "x"}),
    ):
        error, text = await call(env["KAVITA_MCP_URL"], tool, args)
        assert error and "not a report" in text


async def test_traversal_titles_stay_inside(env):
    import httpx

    for title in ("../../x", "/etc/passwd"):
        error, result = await call(
            env["KAVITA_MCP_URL"], "add_document", {"markdown": "# t", "title": title}
        )
        assert not error, result
    async with httpx.AsyncClient(base_url=env["FILEBROWSER_URL"]) as fb:
        token = (await fb.post("/api/login", content=b"{}")).text
        root = (await fb.get("/api/resources/", headers={"X-Auth": token})).json()
        reports = (await fb.get("/api/resources/Reports/", headers={"X-Auth": token})).json()
    assert {i["name"] for i in root["items"]} == {"Books", "Reports"}
    assert {"x", "etc-passwd"} <= {i["name"] for i in reports["items"]}


async def test_symlinked_report_folder_not_followed(env):
    docker_exec("kmcp-it-upload", "ln", "-s", "/etc", "/srv/Reports/evil")
    try:
        await call(env["KAVITA_MCP_URL"], "add_document", {"markdown": "# e", "title": "evil"})
        assert docker_exec("kmcp-it-upload", "test", "-e", "/etc/evil.epub").returncode != 0
    finally:
        docker_exec("kmcp-it-upload", "rm", "-f", "/srv/Reports/evil")


async def test_bad_key_clear_error(env):
    error, text = await call(env["KAVITA_MCP_BADKEY_URL"], "list_library", {})
    assert error and "KAVITA_API_KEY" in text


async def test_non_admin_key_scan_now(env, unique):
    error, result = await call(
        env["KAVITA_MCP_READER_URL"],
        "add_document",
        {"markdown": "# r", "title": f"Reader report {unique}", "scan_now": True},
    )
    assert not error, result
    assert "admin" in result["note"]
    async with Client(env["KAVITA_MCP_READER_URL"]) as client:
        tools = (await client.list_tools()).tools
    assert len(tools) == 10


async def test_volume_title_keeps_full_name(env, kavita, unique):
    title = f"Report v2 Final {unique}"
    sid = (await publish(env, title))["series_id"]
    assert (await kavita.get(f"/api/Series/{sid}")).json()["name"] == title


async def test_quick_republish_is_read_again(env, kavita, unique):
    # Kavita ignores a file change within the same minute as its last scan unless
    # the scan is forced: a re-publish right after publishing must still land.
    title = f"Typo fix {unique}"
    sid = (await publish(env, title, summary="Old summary"))["series_id"]
    error, result = await call(
        env["KAVITA_MCP_URL"],
        "add_document",
        {"markdown": "# Fixed\n\nText.\n", "title": title, "summary": "New summary"},
    )
    assert not error and result["series_id"] == sid

    async def summary_updated():
        r = await kavita.get("/api/Series/metadata", params={"seriesId": sid})
        return r.json()["summary"] == "New summary"

    assert await eventually(summary_updated)
