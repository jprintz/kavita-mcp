import re

import pytest

from kavita_mcp.rows import author_of, progress_pct, read_status, series_row, series_url
from tests.fixtures import load

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


@pytest.mark.parametrize(
    "pages,read,status",
    [
        (10, 0, "unread"),
        (10, 3, "in_progress"),
        (10, 10, "read"),
        (0, 0, "unread"),
        (10, 12, "read"),
    ],
)
def test_read_status(pages, read, status):
    assert read_status(pages, read) == status


def test_progress_pct():
    assert progress_pct(10, 3) == 30
    assert progress_pct(0, 0) == 0
    assert progress_pct(3, 1) == 33
    assert progress_pct(10, 12) == 100


def test_author_of_fixture():
    assert author_of(load("metadata")["body"]) == "Claude"
    assert author_of({"writers": []}) is None
    assert author_of({"writers": [{"name": "A"}, {"name": "B"}]}) == "A, B"


def test_series_row_from_fixture():
    row = series_row(load("series")["body"], author="Claude")
    assert set(row) == ROW_KEYS
    assert row["series_id"] == 3
    assert row["title"] == "Weekly Digest"
    assert row["library"] == "Reports"
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2}", row["added"])
    assert row["read_status"] == "unread"


def test_series_url():
    assert series_url("https://k.example", 2, 7) == "https://k.example/library/2/series/7"
    assert series_url(None, 2, 7) is None
