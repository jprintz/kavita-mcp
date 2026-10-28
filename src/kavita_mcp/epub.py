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
    return replace_entries(epub, {n: css for n in _names(epub) if n.endswith(".css")})


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
