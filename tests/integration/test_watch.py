"""Folder watching alone imports a report (about 6 minutes on Kavita 0.9.1)."""

import pytest

from tests.integration.conftest import call, eventually

pytestmark = pytest.mark.watch


async def test_watch_without_scan_now(env, kavita, unique):
    title = f"Watched {unique}"
    error, result = await call(
        env["KAVITA_MCP_URL"], "add_document", {"markdown": "# w", "title": title}
    )
    assert not error and result["series_id"] is None

    async def imported():
        r = await kavita.post(
            "/api/Series/v2",
            json={
                "statements": [
                    {"field": 24, "comparison": 0, "value": f"/reports/watched-{unique}"}
                ],
                "combination": 1,
            },
        )
        return r.json()

    assert await eventually(imported, timeout=8 * 60, interval=15)
