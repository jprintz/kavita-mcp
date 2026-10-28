import pytest

from kavita_mcp.slug import (
    InvalidSlug,
    InvalidTitle,
    kavita_would_split,
    locate,
    slug_from_kavita_folder,
    slugify,
)


@pytest.mark.parametrize(
    "title,slug",
    [
        ("Q3 Market Report", "q3-market-report"),
        ("Åsa's årsrapport", "asas-arsrapport"),
        ("Résumé — 2026!", "resume-2026"),
        ("../../etc/passwd", "etc-passwd"),
        ("  Spaces   and\ttabs ", "spaces-and-tabs"),
    ],
)
def test_slugify(title, slug):
    assert slugify(title) == slug


def test_slugify_empty_raises():
    with pytest.raises(InvalidTitle):
        slugify("!!! ???")


def test_slugify_caps_length():
    s = slugify("word " * 60)
    assert len(s) <= 80
    assert not s.endswith("-")


@pytest.mark.parametrize("bad", ["../x", "/abs", "a/b", "A", "", "a--b", "-a", "a-", "x" * 81])
def test_locate_rejects(bad):
    with pytest.raises(InvalidSlug):
        locate(bad, fb_root="/Reports", kavita_root="/reports")


def test_locate_paths():
    loc = locate("q3", fb_root="/Reports", kavita_root="/reports")
    assert (loc.slug, loc.fb_dir, loc.fb_file, loc.kavita_dir) == (
        "q3",
        "/Reports/q3/",
        "/Reports/q3/q3.epub",
        "/reports/q3",
    )


@pytest.mark.parametrize(
    "folder,expected",
    [
        ("/reports/q3", "q3"),
        ("/reports/q3/", "q3"),
        ("/books/q3", None),
        ("/reports/a/b", None),
        ("/reports/../books/x", None),
        ("/reports/Q3", None),
        ("/reports", None),
        ("/reports/", None),
        ("", None),
    ],
)
def test_slug_from_kavita_folder(folder, expected):
    assert slug_from_kavita_folder(folder, "/reports") == expected


@pytest.mark.parametrize(
    "title,split",
    [
        ("Report v2 Final", True),
        ("Status Report Vol. 2", True),
        ("Kenichi_v11_c90", True),
        ("Sprint 12 Notes", False),
        ("AI news 2026-10-28: GPT, Claude & more", False),
        ("Q3 Market Report", False),
    ],
)
def test_kavita_would_split(title, split):
    assert kavita_would_split(title) is split
