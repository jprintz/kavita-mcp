import json
import logging

import httpx
from mcp import Client

from kavita_mcp.documents import Publisher
from kavita_mcp.kavita import KavitaAuthError
from kavita_mcp.server import create_server
from tests.fakes import FakeFiles, FakeKavita
from tests.unit.test_documents import SETTINGS, fake_build

EXPECTED = {
    "add_document",
    "update_document",
    "delete_document",
    "list_library",
    "search",
    "get_document",
    "list_collections",
    "want_to_read",
    "reading_status",
    "mark_read",
}
READ_ONLY = {"list_library", "search", "get_document", "list_collections", "reading_status"}
DESTRUCTIVE = {"add_document", "update_document", "delete_document", "want_to_read", "mark_read"}


class FakeReader:
    def __init__(self, error: Exception | None = None):
        self.error = error

    async def list_library(self, **kwargs):
        if self.error:
            raise self.error
        return {"items": [], "total": 0, "page": 1, "next_page": None}


def make(reader=None):
    files = FakeFiles()
    kavita = FakeKavita(files)
    publisher = Publisher(SETTINGS, kavita, files, build=fake_build, poll_interval=0.01)
    return create_server(publisher, reader or FakeReader()), files


def payload(result) -> dict:
    return json.loads(result.content[0].text)


async def test_tools_and_annotations():
    server, _ = make()
    async with Client(server) as client:
        tools = (await client.list_tools()).tools
    assert {t.name for t in tools} == EXPECTED
    for t in tools:
        a = t.annotations
        assert a is not None and (a.title or t.title), t.name
        assert None not in (
            a.read_only_hint,
            a.destructive_hint,
            a.idempotent_hint,
            a.open_world_hint,
        )
        assert a.open_world_hint is False
        assert a.idempotent_hint is True
        assert a.read_only_hint is (t.name in READ_ONLY)
        assert a.destructive_hint is (t.name in DESTRUCTIVE)


async def test_add_document_roundtrip():
    server, files = make()
    async with Client(server) as client:
        result = await client.call_tool("add_document", {"markdown": "# Hi", "title": "Q3 Report"})
    assert not result.is_error
    assert payload(result)["status"] == "published"
    assert "/Reports/q3-report/q3-report.epub" in files.files


async def test_auth_error_text():
    server, _ = make(
        FakeReader(KavitaAuthError("Kavita rejected the API key: check KAVITA_API_KEY"))
    )
    async with Client(server) as client:
        result = await client.call_tool("list_library", {})
    assert result.is_error
    assert "KAVITA_API_KEY" in result.content[0].text


async def test_delete_without_confirm_is_a_clear_error():
    server, _ = make()
    async with Client(server) as client:
        result = await client.call_tool("delete_document", {"series_id": 1, "confirm": False})
    assert result.is_error
    assert "confirm" in result.content[0].text


async def test_logs_never_contain_body(caplog):
    server, _ = make()
    caplog.set_level(logging.DEBUG)
    async with Client(server) as client:
        await client.call_tool(
            "add_document",
            {"markdown": "SECRET-BODY-123", "title": "Q3 Report", "summary": "SECRET-SUM"},
        )
    assert "SECRET-BODY-123" not in caplog.text
    assert "SECRET-SUM" not in caplog.text
    assert "tool=add_document" in caplog.text


async def test_update_description_mentions_republish():
    server, _ = make()
    async with Client(server) as client:
        tools = {t.name: t for t in (await client.list_tools()).tools}
    assert "add_document" in tools["update_document"].description
    assert "data:" in tools["add_document"].description


async def test_healthz():
    server, _ = make()
    app = server.streamable_http_app()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as http:
        r = await http.get("/healthz")
    assert r.status_code == 200
    assert r.json() == {"kavita": True, "library": True, "upload": True}
