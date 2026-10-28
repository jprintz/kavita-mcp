import base64
import io
import zipfile

import pytest

from kavita_mcp.epub import (
    DocumentMeta,
    ImageTooLarge,
    MarkdownTooLarge,
    build_epub,
    read_opf,
)

DC = "{http://purl.org/dc/elements/1.1/}"
OPF = "{http://www.idpf.org/2007/opf}"
BIG = 10 * 1024 * 1024
PNG_1PX = "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNkYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg=="

META = DocumentMeta(
    title="Åsa's årsrapport",
    author="Claude",
    language="sv",
    date="2026-10-28",
    summary="Kort sammanfattning",
    collection="Research",
    slug="asas-arsrapport",
)
MD = "# Intro\n\nText.\n\n# Results\n\nMore text.\n"


def build(md=MD, meta=META, pandoc="pandoc", **caps):
    caps.setdefault("max_markdown_bytes", BIG)
    caps.setdefault("max_image_bytes", BIG)
    return build_epub(md, meta, pandoc=pandoc, **caps)


def titles(root) -> dict[str, str]:
    """dc:title texts by their title-type refinement (main, collection)."""
    kinds = {
        m.get("refines", "").lstrip("#"): m.text
        for m in root.iter(f"{OPF}meta")
        if m.get("property") == "title-type"
    }
    return {kinds.get(t.get("id"), "main"): t.text for t in root.iter(f"{DC}title")}


def entries(epub: bytes) -> dict[str, bytes]:
    with zipfile.ZipFile(io.BytesIO(epub)) as z:
        return {n: z.read(n) for n in z.namelist()}


def xhtml_text(epub: bytes) -> str:
    return "".join(v.decode("utf-8") for k, v in entries(epub).items() if k.endswith(".xhtml"))


def test_opf_metadata(pandoc_bin):
    _, root = read_opf(build(pandoc=pandoc_bin))
    assert titles(root) == {"main": "Åsa's årsrapport", "collection": "Research"}
    assert [c.text for c in root.iter(f"{DC}creator")] == ["Claude"]
    assert root.find(f".//{DC}language").text == "sv"
    assert root.find(f".//{DC}date").text.startswith("2026-10-28")
    assert root.find(f".//{DC}description").text == "Kort sammanfattning"
    assert "urn:kavita-mcp:asas-arsrapport" in [i.text for i in root.iter(f"{DC}identifier")]


def test_no_collection_title_when_none(pandoc_bin):
    meta = DocumentMeta(**{**META.__dict__, "collection": None, "summary": None})
    _, root = read_opf(build(meta=meta, pandoc=pandoc_bin))
    assert len(list(root.iter(f"{DC}title"))) == 1
    assert root.find(f".//{DC}description") is None


def test_no_title_page(pandoc_bin):
    assert not [n for n in entries(build(pandoc=pandoc_bin)) if "title_page" in n]


def test_toc_from_headings(pandoc_bin):
    nav = [v for k, v in entries(build(pandoc=pandoc_bin)).items() if k.endswith("nav.xhtml")]
    assert nav and b"Intro" in nav[0] and b"Results" in nav[0]


def test_front_matter_in_markdown_does_not_override(pandoc_bin):
    md = "---\ntitle: Evil\nauthor: Mallory\n---\n\n% Evil2\n\n# A\n"
    _, root = read_opf(build(md=md, pandoc=pandoc_bin))
    assert titles(root)["main"] == META.title
    assert [c.text for c in root.iter(f"{DC}creator")] == ["Claude"]


def test_remote_and_local_images_become_alt_text(pandoc_bin):
    md = (
        "![remote](https://example.com/x.png)\n\n"
        "![local](/etc/passwd)\n\n"
        "<img src='https://example.com/y.png'>\n"
    )
    epub = build(md=md, pandoc=pandoc_bin)
    assert not [n for n in entries(epub) if n.endswith((".png", ".jpg", ".gif", ".svg"))]
    text = xhtml_text(epub)
    assert "example.com" not in text and "/etc/passwd" not in text
    assert "remote" in text and "local" in text


def test_data_uri_image_embedded(pandoc_bin):
    epub = build(md=f"![dot](data:image/png;base64,{PNG_1PX})\n", pandoc=pandoc_bin)
    assert len([n for n in entries(epub) if n.endswith(".png")]) == 1


def test_markdown_cap():
    with pytest.raises(MarkdownTooLarge):
        build(md="x" * 101, max_markdown_bytes=100)


def test_markdown_cap_counts_bytes_not_characters():
    with pytest.raises(MarkdownTooLarge):
        build(md="å" * 60, max_markdown_bytes=100)


def test_image_cap():
    payload = base64.b64encode(b"\0" * 2000).decode()
    with pytest.raises(ImageTooLarge):
        build(md=f"![x](data:image/png;base64,{payload})\n", max_image_bytes=1000)


@pytest.mark.epubcheck
def test_epubcheck_valid(pandoc_bin, epubcheck):
    md = MD + f"\n![dot](data:image/png;base64,{PNG_1PX})\n\n| a | b |\n|---|---|\n| 1 | 2 |\n"
    epubcheck(build(md=md, pandoc=pandoc_bin))


def test_metadata_text_is_literal(pandoc_bin):
    weird = "C# *notes* [draft] 'quoted' \"q\" a_b `code` <b> 50% & more\\"
    meta = DocumentMeta(
        **{
            **META.__dict__,
            "title": weird,
            "author": "Ann *A*",
            "summary": weird,
            "collection": "R&D 'x'",
        }
    )
    _, root = read_opf(build(meta=meta, pandoc=pandoc_bin))
    assert titles(root) == {"main": weird, "collection": "R&D 'x'"}
    assert [c.text for c in root.iter(f"{DC}creator")] == ["Ann *A*"]
    assert root.find(f".//{DC}description").text == weird
