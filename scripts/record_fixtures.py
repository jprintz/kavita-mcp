"""Record Kavita 0.9.1 API responses for the unit tests (tests/fixtures/kavita/).

Needs the throwaway (scripts/throwaway.sh up) and pandoc. Publishes three reports
through File Browser, scans the Reports library, puts one in progress, marks one
read and one want-to-read, then saves the responses kavita-mcp reads. Keys are
removed from what is saved.

    PANDOC=... uv run python scripts/record_fixtures.py
"""

import json
import os
import time
from pathlib import Path

import httpx

from kavita_mcp.epub import DocumentMeta, build_epub

OUT = Path("tests/fixtures/kavita")
SAMPLES = [
    ("Weekly Digest", "weekly-digest", "Weekly", "What happened this week."),
    ("Market Report", "market-report", None, "Prices and volumes."),
    ("Research Notes", "research-notes", None, None),
]


def load_env() -> dict[str, str]:
    env = {}
    for line in Path(".throwaway/env").read_text().splitlines():
        key, _, value = line.partition("=")
        env[key] = value
    return env


def main() -> None:
    env = load_env()
    secrets_ = {env["KAVITA_API_KEY"], env["KAVITA_NONADMIN_KEY"]}
    kavita = httpx.Client(
        base_url=env["KAVITA_URL"], headers={"x-api-key": env["KAVITA_API_KEY"]}, timeout=30
    )
    files = httpx.Client(base_url=env["FILEBROWSER_URL"], timeout=30)
    token = files.post("/api/login", content="{}").raise_for_status().text
    reports = int(env["REPORTS_LIBRARY_ID"])

    for title, slug, collection, summary in SAMPLES:
        meta = DocumentMeta(
            title=title,
            author="Claude",
            language="en",
            date="2026-10-28",
            summary=summary,
            collection=collection,
            slug=slug,
        )
        md = "# One\n\n" + "Words here. " * 200 + "\n\n# Two\n\n" + "More words. " * 200 + "\n"
        epub = build_epub(
            md,
            meta,
            pandoc=os.environ.get("PANDOC", "pandoc"),
            max_markdown_bytes=10**6,
            max_image_bytes=10**6,
        )
        files.post(
            f"/api/resources/Reports/{slug}/{slug}.epub",
            params={"override": "true"},
            headers={"X-Auth": token},
            content=epub,
        ).raise_for_status()

    kavita.post("/api/Library/scan", params={"libraryId": reports}).raise_for_status()
    ids: dict[str, int] = {}
    for _ in range(60):
        for _title, slug, _c, _s in SAMPLES:
            r = kavita.post(
                "/api/Series/v2",
                json={
                    "statements": [{"field": 24, "comparison": 0, "value": f"/reports/{slug}"}],
                    "combination": 1,
                    "limitTo": 0,
                },
            ).raise_for_status()
            if r.json():
                ids[slug] = r.json()[0]["id"]
        if len(ids) == len(SAMPLES):
            break
        time.sleep(1)
    else:
        raise SystemExit(f"Kavita imported only {sorted(ids)}")

    weekly, market, research = (ids[s[1]] for s in SAMPLES)
    detail = kavita.get("/api/Series/series-detail", params={"seriesId": market}).json()
    chapter = (detail["specials"] or detail["chapters"])[0]
    kavita.post(
        "/api/Reader/progress",
        json={
            "volumeId": chapter["volumeId"],
            "chapterId": chapter["id"],
            "pageNum": 1,
            "seriesId": market,
            "libraryId": reports,
        },
    ).raise_for_status()
    kavita.post("/api/Reader/mark-read", json={"seriesId": research}).raise_for_status()
    kavita.post("/api/want-to-read/add-series", json={"seriesIds": [weekly]}).raise_for_status()

    v2_body = {
        "statements": [],
        "combination": 1,
        "sortOptions": {"sortField": 2, "isAscending": False},
        "limitTo": 0,
    }
    calls = {
        "libraries": ("GET", "/api/Library/libraries", None, None),
        "series": ("GET", f"/api/Series/{weekly}", None, None),
        "metadata": ("GET", "/api/Series/metadata", {"seriesId": weekly}, None),
        "series_detail": ("GET", "/api/Series/series-detail", {"seriesId": weekly}, None),
        "series_v2_page1": ("POST", "/api/Series/v2", {"PageNumber": 1, "PageSize": 2}, v2_body),
        "search": (
            "GET",
            "/api/Search/search",
            {"queryString": "weekly", "includeChapterAndFiles": "false"},
            None,
        ),
        "on_deck": ("POST", "/api/Series/on-deck", {"PageNumber": 1, "PageSize": 20}, None),
        "want_to_read_v2": (
            "POST",
            "/api/want-to-read/v2",
            {"PageNumber": 1, "PageSize": 20},
            {"statements": [], "combination": 1, "limitTo": 0},
        ),
        "collections": ("GET", "/api/Collection", {"ownedOnly": "false"}, None),
        "collections_for_series": ("GET", "/api/Collection/all-series", {"seriesId": weekly}, None),
    }
    OUT.mkdir(parents=True, exist_ok=True)
    for name, (method, path, params, body) in calls.items():
        r = kavita.request(method, path, params=params, json=body).raise_for_status()
        text = r.text
        for secret in secrets_:
            text = text.replace(secret, "REDACTED")
        record = {
            "request": {"method": method, "path": path, "params": params, "json": body},
            "status": r.status_code,
            "headers": {k: r.headers[k] for k in ("Pagination",) if k in r.headers},
            "body": json.loads(text) if text else None,
        }
        (OUT / f"{name}.json").write_text(json.dumps(record, indent=2, ensure_ascii=False) + "\n")
    print(f"recorded {len(calls)} fixtures; series ids {ids}")


if __name__ == "__main__":
    main()
