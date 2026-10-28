import pytest

from kavita_mcp.config import Settings
from kavita_mcp.documents import ConfirmationRequired, NotAManagedReport, Publisher
from kavita_mcp.epub import read_info, rewrite_metadata
from kavita_mcp.kavita import KavitaForbidden, KavitaUnreachable
from kavita_mcp.slug import InvalidTitle
from tests.conftest import FIXTURES
from tests.fakes import BOOKS_ID, FakeFiles, FakeKavita

SAMPLE = (FIXTURES / "sample.epub").read_bytes()
SETTINGS = Settings.from_env(
    {
        "KAVITA_API_KEY": "k" * 20,
        "KAVITA_PUBLIC_URL": "https://k.example",
        "SCAN_TIMEOUT_SECONDS": "1",
    }
)


def fake_build(markdown, meta, **_caps):
    """A real EPUB (the sample) carrying the requested summary and collection."""
    return rewrite_metadata(SAMPLE, summary=meta.summary, collection=meta.collection)


def make(**kavita_kwargs):
    files = FakeFiles()
    kavita = FakeKavita(files, **kavita_kwargs)
    publisher = Publisher(SETTINGS, kavita, files, build=fake_build, poll_interval=0.01)
    return publisher, kavita, files


async def test_add_new_without_scan():
    publisher, kavita, files = make()
    result = await publisher.add(markdown="# Hi", title="Q3 Report")
    assert result["status"] == "published"
    assert result["series_id"] is None
    assert result["slug"] == "q3-report"
    assert result["path"] == "/reports/q3-report/q3-report.epub"
    assert "about 6 minutes" in result["note"]
    assert list(files.files) == ["/Reports/q3-report/q3-report.epub"]
    assert kavita.scans == 0


async def test_add_existing_returns_series_id():
    publisher, kavita, files = make()
    files.files["/Reports/q3-report/q3-report.epub"] = SAMPLE
    sid = kavita.put("/reports/q3-report")
    result = await publisher.add(markdown="# Hi", title="Q3 Report")
    assert result["status"] == "updated"
    assert result["series_id"] == sid
    assert result["url"] == f"https://k.example/library/2/series/{sid}"


async def test_add_same_slug_different_title_reports_updated():
    publisher, _, files = make()
    await publisher.add(markdown="# A", title="Q3 Report")
    second = await publisher.add(markdown="# B", title="Q3 report!")
    assert second["status"] == "updated"
    assert len(files.files) == 1


async def test_add_passes_metadata_to_build():
    seen = {}

    def build(markdown, meta, **caps):
        seen.update(meta=meta, caps=caps)
        return SAMPLE

    files = FakeFiles()
    publisher = Publisher(SETTINGS, FakeKavita(files), files, build=build)
    await publisher.add(
        markdown="# x",
        title="  Åsa's årsrapport ",
        collection=" Research ",
        summary="S",
        language="sv",
    )
    meta = seen["meta"]
    assert (meta.title, meta.author, meta.collection, meta.summary, meta.language, meta.slug) == (
        "Åsa's årsrapport",
        "Claude",
        "Research",
        "S",
        "sv",
        "asas-arsrapport",
    )
    assert seen["caps"] == {
        "max_markdown_bytes": SETTINGS.max_markdown_bytes,
        "max_image_bytes": SETTINGS.max_image_bytes,
    }


@pytest.mark.parametrize("title", ["", "   ", "x" * 201])
async def test_add_rejects_bad_title(title):
    publisher, _, files = make()
    with pytest.raises(InvalidTitle):
        await publisher.add(markdown="# x", title=title)
    assert files.files == {}


async def test_add_scan_now_waits_for_series():
    publisher, kavita, _ = make()
    result = await publisher.add(markdown="# Hi", title="Q3 Report", scan_now=True)
    assert kavita.scans == 1
    sid = result["series_id"]
    assert sid is not None
    assert result["url"] == f"https://k.example/library/2/series/{sid}"


async def test_add_scan_now_timeout_says_file_in_place():
    publisher, _, files = make(imports=False)
    result = await publisher.add(markdown="# Hi", title="Q3 Report", scan_now=True)
    assert result["series_id"] is None
    assert "in place" in result["note"]
    assert files.files


async def test_add_scan_now_kavita_down_after_upload():
    publisher, kavita, files = make()
    kavita.scan_error = KavitaUnreachable("Kavita is unreachable at http://kavita:5000: refused")
    result = await publisher.add(markdown="# Hi", title="Q3 Report", scan_now=True)
    assert files.files
    assert "in place" in result["note"] and "Kavita" in result["note"]


async def test_add_scan_now_forbidden_note():
    publisher, kavita, files = make()
    kavita.scan_error = KavitaForbidden("Kavita refused this: it needs an admin key")
    result = await publisher.add(markdown="# Hi", title="Q3 Report", scan_now=True)
    assert files.files
    assert "admin" in result["note"]


async def test_update_refuses_books_series():
    publisher, kavita, _ = make()
    sid = kavita.put("/books/x", library_id=BOOKS_ID)
    with pytest.raises(NotAManagedReport):
        await publisher.update(sid, summary="x")


async def test_update_moves_collection():
    publisher, kavita, files = make()
    files.files["/Reports/weekly-digest/weekly-digest.epub"] = SAMPLE  # collection "Weekly"
    sid = kavita.put("/reports/weekly-digest")
    result = await publisher.update(sid, collection="Research")
    assert kavita.removed == [("Weekly", sid)]
    assert kavita.added == [("Research", sid)]
    assert (
        read_info(files.files["/Reports/weekly-digest/weekly-digest.epub"]).collection == "Research"
    )
    assert result["collection"] == "Research"


async def test_update_clears_collection():
    publisher, kavita, files = make()
    files.files["/Reports/weekly-digest/weekly-digest.epub"] = SAMPLE
    sid = kavita.put("/reports/weekly-digest")
    await publisher.update(sid, collection="")
    assert kavita.removed == [("Weekly", sid)]
    assert kavita.added == []
    assert read_info(files.files["/Reports/weekly-digest/weekly-digest.epub"]).collection is None


async def test_update_summary_keeps_collection():
    publisher, kavita, files = make()
    files.files["/Reports/weekly-digest/weekly-digest.epub"] = SAMPLE
    sid = kavita.put("/reports/weekly-digest")
    result = await publisher.update(sid, summary="New summary")
    info = read_info(files.files["/Reports/weekly-digest/weekly-digest.epub"])
    assert (info.summary, info.collection) == ("New summary", "Weekly")
    assert kavita.added == kavita.removed == []
    assert result["summary"] == "New summary"


async def test_update_needs_a_change():
    publisher, kavita, files = make()
    files.files["/Reports/weekly-digest/weekly-digest.epub"] = SAMPLE
    sid = kavita.put("/reports/weekly-digest")
    with pytest.raises(ValueError):
        await publisher.update(sid)


async def test_delete_requires_confirm():
    publisher, kavita, files = make()
    files.files["/Reports/q3/q3.epub"] = SAMPLE
    sid = kavita.put("/reports/q3")
    with pytest.raises(ConfirmationRequired):
        await publisher.delete(sid, confirm=False)
    assert files.files


async def test_delete_removes_folder():
    publisher, kavita, files = make()
    files.files["/Reports/q3/q3.epub"] = SAMPLE
    sid = kavita.put("/reports/q3")
    result = await publisher.delete(sid, confirm=True, scan_now=True)
    assert files.files == {}
    assert sid not in kavita.series_by_id
    assert result["deleted"] is True


async def test_delete_refuses_books_series():
    publisher, kavita, _ = make()
    sid = kavita.put("/books/q3", library_id=BOOKS_ID)
    with pytest.raises(NotAManagedReport):
        await publisher.delete(sid, confirm=True)


# Kavita re-reads a book only when the file's mtime, cut to the minute, is later
# than its last scan (0.9.1.4, CacheHelper.IsFileUnmodifiedSinceCreationOrLastScan):
# a change within the same minute is ignored unless the scan is forced.


def scanned(kavita, sid, seconds_ago: float):
    from datetime import UTC, datetime, timedelta

    when = datetime.now(UTC) - timedelta(seconds=seconds_ago)
    kavita.series_by_id[sid]["lastFolderScanned"] = when.replace(tzinfo=None).isoformat()


async def test_update_scan_now_forces_series_scan():
    publisher, kavita, files = make()
    files.files["/Reports/weekly-digest/weekly-digest.epub"] = SAMPLE
    sid = kavita.put("/reports/weekly-digest")
    scanned(kavita, sid, 3600)
    result = await publisher.update(sid, summary="x", scan_now=True)
    assert kavita.series_scans == [(2, sid)]
    assert "re-reading" in result["note"]


async def test_update_soon_after_scan_forces_series_scan():
    publisher, kavita, files = make()
    files.files["/Reports/weekly-digest/weekly-digest.epub"] = SAMPLE
    sid = kavita.put("/reports/weekly-digest")
    scanned(kavita, sid, 5)
    await publisher.update(sid, summary="x")
    assert kavita.series_scans == [(2, sid)]


async def test_update_long_after_scan_relies_on_watcher():
    publisher, kavita, files = make()
    files.files["/Reports/weekly-digest/weekly-digest.epub"] = SAMPLE
    sid = kavita.put("/reports/weekly-digest")
    scanned(kavita, sid, 3600)
    result = await publisher.update(sid, summary="x")
    assert kavita.series_scans == []
    assert "about 6 minutes" in result["note"]


async def test_republish_soon_after_scan_forces_series_scan():
    publisher, kavita, files = make()
    files.files["/Reports/q3-report/q3-report.epub"] = SAMPLE
    sid = kavita.put("/reports/q3-report")
    scanned(kavita, sid, 5)
    result = await publisher.add(markdown="# Fixed", title="Q3 Report")
    assert kavita.series_scans == [(2, sid)]
    assert result["series_id"] == sid


async def test_forced_scan_forbidden_note():
    publisher, kavita, files = make()
    files.files["/Reports/weekly-digest/weekly-digest.epub"] = SAMPLE
    sid = kavita.put("/reports/weekly-digest")
    scanned(kavita, sid, 5)
    kavita.scan_error = KavitaForbidden("Kavita refused this: it needs an admin key")
    result = await publisher.update(sid, summary="x")
    assert "admin" in result["note"] and "minute" in result["note"]


async def test_add_scan_now_repeats_a_dropped_scan_request():
    # Kavita drops a library-scan request while another scan of that library runs
    # ("A duplicate request for Library Scan ... Skipping", TaskScheduler.cs).
    files = FakeFiles()
    kavita = FakeKavita(files)
    kavita.drop_scans = 2
    publisher = Publisher(
        SETTINGS, kavita, files, build=fake_build, poll_interval=0.01, rescan_interval=0.05
    )
    result = await publisher.add(markdown="# Hi", title="Q3 Report", scan_now=True)
    assert result["series_id"] is not None
    assert kavita.scans == 3
