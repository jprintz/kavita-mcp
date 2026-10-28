"""Markdown to EPUB 3 with pandoc, and reading the EPUB's package metadata."""

import base64
import binascii
import io
import json
import re
import subprocess
import tempfile
import zipfile
from dataclasses import dataclass
from importlib import resources
from pathlib import Path
from xml.etree import ElementTree

from kavita_mcp.errors import KavitaMcpError
from kavita_mcp.slug import kavita_would_split

DATA_URI_RE = re.compile(r"data:image/[A-Za-z0-9.+-]+;base64,([A-Za-z0-9+/=\s]+)")


class MarkdownTooLarge(KavitaMcpError):
    pass


class ImageTooLarge(KavitaMcpError):
    pass


class PandocError(KavitaMcpError):
    pass


class InvalidEpub(KavitaMcpError):
    pass


@dataclass(frozen=True)
class DocumentMeta:
    title: str
    author: str
    language: str
    date: str  # YYYY-MM-DD
    summary: str | None
    collection: str | None
    slug: str


def build_epub(
    markdown: str,
    meta: DocumentMeta,
    *,
    pandoc: str = "pandoc",
    max_markdown_bytes: int,
    max_image_bytes: int,
    timeout: float = 60.0,
) -> bytes:
    size = len(markdown.encode("utf-8"))
    if size > max_markdown_bytes:
        raise MarkdownTooLarge(
            f"The markdown is {size} bytes; the limit is {max_markdown_bytes} bytes"
        )
    for match in DATA_URI_RE.finditer(markdown):
        try:
            image = base64.b64decode(re.sub(r"\s", "", match.group(1)))
        except binascii.Error:
            continue  # not valid base64: pandoc keeps it as a broken image
        if len(image) > max_image_bytes:
            raise ImageTooLarge(
                f"An embedded image is {len(image)} bytes; the limit is {max_image_bytes} bytes"
            )

    with tempfile.TemporaryDirectory() as tmp:
        work = Path(tmp)
        (work / "input.md").write_text(markdown, encoding="utf-8")
        (work / "meta.yaml").write_text(json.dumps(_pandoc_metadata(meta)), encoding="utf-8")
        package = resources.files("kavita_mcp")
        (work / "images.lua").write_bytes(package.joinpath("images.lua").read_bytes())
        (work / "data").mkdir()
        command = [
            pandoc,
            "--sandbox",
            "--data-dir=data",
            # A YAML block or "% title" lines in the markdown must not replace the
            # title and author: they stay body text.
            "--from=markdown-yaml_metadata_block-pandoc_title_block",
            "--to=epub3",
            "--toc",
            "--split-level=1",
            "--epub-title-page=false",
            "--lua-filter=images.lua",
            "--metadata-file=meta.yaml",
            "--output=out.epub",
            "input.md",
        ]
        try:
            result = subprocess.run(
                command, cwd=work, capture_output=True, text=True, timeout=timeout
            )
        except subprocess.TimeoutExpired:
            raise PandocError(f"pandoc took longer than {timeout:.0f} s") from None
        except FileNotFoundError:
            raise PandocError(f"pandoc not found at {pandoc!r}") from None
        if result.returncode != 0:
            head = "\n".join(result.stderr.strip().splitlines()[:5])
            raise PandocError(f"pandoc failed: {head}")
        epub = (work / "out.epub").read_bytes()
    # --sandbox ignores --css and the data dir, so pandoc writes its built-in
    # stylesheet; swap in ours.
    css = package.joinpath("epub.css").read_bytes()
    new = {n: css for n in _names(epub) if n.endswith(".css")}
    if kavita_would_split(meta.title):
        opf_path, root = read_opf(epub)
        new[opf_path] = _with_explicit_series(root, meta.title)
    return replace_entries(epub, new)


# Kavita's "loose leaf" volume number (ParserConstants.LooseLeafVolume, 0.9.1.4).
LOOSE_LEAF_INDEX = "-100000"


def _with_explicit_series(root: ElementTree.Element, title: str) -> bytes:
    """The package document with calibre:series = the title, as a loose-leaf book.

    Kavita cuts a title with a volume marker ("Report v2 Final" -> series
    "Report", volume 2) and merges every such book into one series. An explicit
    series with the loose-leaf index keeps the full title and a single book
    (checked live on 0.9.1.4). Only for those titles: KOReader shows the index.
    """
    metadata = _metadata(root)
    for name, content in (("calibre:series", title), ("calibre:series_index", LOOSE_LEAF_INDEX)):
        metadata.append(ElementTree.Element(f"{OPF}meta", {"name": name, "content": content}))
    return ElementTree.tostring(root, encoding="utf-8", xml_declaration=True)


def _literal(text: str) -> str:
    """Escape text so pandoc's markdown reader keeps it as is.

    pandoc reads metadata values as markdown: "'" would become a curly quote and
    "*x*" emphasis. Every ASCII punctuation character can be backslash-escaped.
    """
    return re.sub(r"([!-/:-@\[-`{-~])", r"\\\1", text)


def _pandoc_metadata(meta: DocumentMeta) -> dict:
    title = [{"type": "main", "text": _literal(meta.title)}]
    if meta.collection:
        # An EPUB3 collection title: Kavita puts the book in that collection.
        title.append({"type": "collection", "text": _literal(meta.collection)})
    data = {
        "title": title,
        "author": _literal(meta.author),
        "lang": meta.language,
        "date": meta.date,
        "identifier": [{"scheme": "URN", "text": f"urn:kavita-mcp:{meta.slug}"}],
    }
    if meta.summary:
        data["description"] = _literal(meta.summary)
    return data


def _names(epub: bytes) -> list[str]:
    try:
        with zipfile.ZipFile(io.BytesIO(epub)) as z:
            return z.namelist()
    except zipfile.BadZipFile:
        raise InvalidEpub("The file is not a readable EPUB") from None


def replace_entries(epub: bytes, new: dict[str, bytes]) -> bytes:
    """A copy of the EPUB with some entries' contents replaced.

    "mimetype" stays first and stored, as the EPUB spec requires; every other
    entry keeps its name, order and compression.
    """
    out = io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(epub)) as src, zipfile.ZipFile(out, "w") as dst:
        infos = sorted(src.infolist(), key=lambda i: i.filename != "mimetype")
        for info in infos:
            data = new.get(info.filename, src.read(info.filename))
            compress = zipfile.ZIP_STORED if info.filename == "mimetype" else info.compress_type
            dst.writestr(info, data, compress_type=compress)
    return out.getvalue()


def read_opf(epub: bytes) -> tuple[str, ElementTree.Element]:
    """The package document's path inside the zip, and its parsed root."""
    try:
        with zipfile.ZipFile(io.BytesIO(epub)) as z:
            container = ElementTree.fromstring(z.read("META-INF/container.xml"))
            rootfile = container.find(".//{*}rootfile")
            if rootfile is None:
                raise InvalidEpub("The EPUB has no package document")
            path = rootfile.get("full-path", "")
            return path, ElementTree.fromstring(z.read(path))
    except (zipfile.BadZipFile, KeyError, ElementTree.ParseError):
        raise InvalidEpub("The file is not a readable EPUB") from None


OPF_NS = "http://www.idpf.org/2007/opf"
DC_NS = "http://purl.org/dc/elements/1.1/"
OPF = f"{{{OPF_NS}}}"
DC = f"{{{DC_NS}}}"
# Serialise the package document with its usual prefixes (OPF as the default
# namespace, dc:) rather than ns0:/ns1:.
ElementTree.register_namespace("", OPF_NS)
ElementTree.register_namespace("dc", DC_NS)


class _Keep:
    def __repr__(self) -> str:
        return "KEEP"


KEEP = _Keep()


@dataclass(frozen=True)
class EpubInfo:
    title: str
    author: str | None
    summary: str | None
    collection: str | None
    language: str | None


def _title_types(metadata: ElementTree.Element) -> dict[str, str]:
    """dc:title id -> its title-type refinement (main, collection, ...)."""
    return {
        m.get("refines", "").lstrip("#"): (m.text or "")
        for m in metadata.findall(f"{OPF}meta")
        if m.get("property") == "title-type"
    }


def _collection_title(metadata: ElementTree.Element) -> ElementTree.Element | None:
    types = _title_types(metadata)
    for t in metadata.findall(f"{DC}title"):
        if types.get(t.get("id", "")) == "collection":
            return t
    return None


def _metadata(root: ElementTree.Element) -> ElementTree.Element:
    metadata = root.find(f"{OPF}metadata")
    if metadata is None:
        raise InvalidEpub("The EPUB's package document has no metadata")
    return metadata


def read_info(epub: bytes) -> EpubInfo:
    _, root = read_opf(epub)
    metadata = _metadata(root)
    types = _title_types(metadata)
    titles = metadata.findall(f"{DC}title")
    main = next((t for t in titles if types.get(t.get("id", ""), "main") == "main"), None)
    collection = _collection_title(metadata)

    def text(tag: str) -> str | None:
        el = metadata.find(f"{DC}{tag}")
        return el.text if el is not None and el.text else None

    return EpubInfo(
        title=(main.text or "") if main is not None else "",
        author=text("creator"),
        summary=text("description"),
        collection=collection.text if collection is not None else None,
        language=text("language"),
    )


def rewrite_metadata(epub: bytes, *, summary=KEEP, collection=KEEP) -> bytes:
    """A copy of the EPUB with a new summary and/or collection ("" or None removes it).

    Only the package document changes; every other entry is copied byte for byte.
    """
    opf_path, root = read_opf(epub)
    metadata = _metadata(root)

    if summary is not KEEP:
        for el in metadata.findall(f"{DC}description"):
            metadata.remove(el)
        if summary:
            el = ElementTree.Element(f"{DC}description")
            el.text = summary
            _insert_after_titles(metadata, el)

    if collection is not KEEP:
        old = _collection_title(metadata)
        if old is not None:
            ref = "#" + old.get("id", "")
            for m in metadata.findall(f"{OPF}meta"):
                if m.get("refines") == ref:
                    metadata.remove(m)
            metadata.remove(old)
        if collection:
            title = ElementTree.Element(f"{DC}title", {"id": "kavita-mcp-collection"})
            title.text = collection
            refine = ElementTree.Element(
                f"{OPF}meta", {"refines": "#kavita-mcp-collection", "property": "title-type"}
            )
            refine.text = "collection"
            _insert_after_titles(metadata, title, refine)

    xml = ElementTree.tostring(root, encoding="utf-8", xml_declaration=True)
    return replace_entries(epub, {opf_path: xml})


def _insert_after_titles(metadata: ElementTree.Element, *elements: ElementTree.Element) -> None:
    children = list(metadata)
    last = max(
        (
            i
            for i, c in enumerate(children)
            if c.tag == f"{DC}title" or c.get("refines", "").startswith("#epub-title")
        ),
        default=len(children) - 1,
    )
    for offset, el in enumerate(elements, start=1):
        metadata.insert(last + offset, el)
