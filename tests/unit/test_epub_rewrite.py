import io
import zipfile

import pytest

from kavita_mcp.epub import InvalidEpub, read_info, read_opf, rewrite_metadata
from tests.conftest import FIXTURES

SAMPLE = (FIXTURES / "sample.epub").read_bytes()


def entries(epub: bytes) -> dict[str, bytes]:
    with zipfile.ZipFile(io.BytesIO(epub)) as z:
        return {n: z.read(n) for n in z.namelist()}


def test_read_info():
    info = read_info(SAMPLE)
    assert info.title == "Weekly Digest"
    assert info.author == "Claude"
    assert info.summary == "Sample summary"
    assert info.collection == "Weekly"
    assert info.language == "en"


def test_rewrite_summary():
    assert read_info(rewrite_metadata(SAMPLE, summary="New")).summary == "New"


def test_rewrite_clears_summary():
    assert read_info(rewrite_metadata(SAMPLE, summary="")).summary is None


def test_rewrite_moves_collection():
    out = rewrite_metadata(SAMPLE, collection="Research")
    info = read_info(out)
    assert info.collection == "Research"
    assert info.title == "Weekly Digest"
    _, root = read_opf(out)
    assert len(list(root.iter("{http://purl.org/dc/elements/1.1/}title"))) == 2


def test_rewrite_clears_collection():
    out = rewrite_metadata(SAMPLE, collection="")
    assert read_info(out).collection is None
    _, root = read_opf(out)
    assert len(list(root.iter("{http://purl.org/dc/elements/1.1/}title"))) == 1


def test_rewrite_adds_collection_when_none():
    plain = rewrite_metadata(SAMPLE, collection=None)
    assert read_info(rewrite_metadata(plain, collection="New one")).collection == "New one"


def test_keep_leaves_fields():
    info = read_info(rewrite_metadata(SAMPLE, summary="x"))
    assert info.collection == "Weekly"
    assert read_info(rewrite_metadata(SAMPLE, collection="y")).summary == "Sample summary"


def test_other_entries_byte_identical():
    opf_path, _ = read_opf(SAMPLE)
    before, after = entries(SAMPLE), entries(rewrite_metadata(SAMPLE, summary="x"))
    assert before.keys() == after.keys()
    assert {k: v for k, v in before.items() if k != opf_path} == {
        k: v for k, v in after.items() if k != opf_path
    }


def test_mimetype_first_and_stored():
    with zipfile.ZipFile(io.BytesIO(rewrite_metadata(SAMPLE, summary="x"))) as z:
        first = z.infolist()[0]
    assert first.filename == "mimetype"
    assert first.compress_type == zipfile.ZIP_STORED


def test_not_an_epub():
    with pytest.raises(InvalidEpub):
        read_info(b"nope")
    with pytest.raises(InvalidEpub):
        rewrite_metadata(b"nope", summary="x")


@pytest.mark.epubcheck
def test_rewritten_epub_valid(epubcheck):
    epubcheck(rewrite_metadata(SAMPLE, summary="New", collection="Research & more"))
