"""Slugs, and the only file paths kavita-mcp ever builds from them."""

import posixpath
import re
import unicodedata
from dataclasses import dataclass

from kavita_mcp.errors import KavitaMcpError

MAX_SLUG = 80
SLUG_RE = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")


class InvalidTitle(KavitaMcpError):
    pass


class InvalidSlug(KavitaMcpError):
    pass


@dataclass(frozen=True)
class ReportLocation:
    slug: str
    fb_dir: str  # File Browser: /Reports/<slug>/
    fb_file: str  # File Browser: /Reports/<slug>/<slug>.epub
    kavita_dir: str  # Kavita's folderPath: /reports/<slug>


def slugify(title: str) -> str:
    text = unicodedata.normalize("NFKD", title)
    text = text.encode("ascii", "ignore").decode("ascii").lower()
    text = re.sub(r"['`]", "", text)  # "Åsa's" -> "asas", not "asa-s"
    text = re.sub(r"[^a-z0-9]+", "-", text).strip("-")
    text = text[:MAX_SLUG].rstrip("-")
    if not text:
        raise InvalidTitle(f"The title {title!r} has no letters or digits to name its folder with")
    return text


def _check(slug: str) -> None:
    if len(slug) > MAX_SLUG or not SLUG_RE.match(slug):
        raise InvalidSlug(f"Not a valid report slug: {slug!r}")


def locate(slug: str, *, fb_root: str, kavita_root: str) -> ReportLocation:
    _check(slug)
    fb_root = fb_root.rstrip("/")
    kavita_root = kavita_root.rstrip("/")
    return ReportLocation(
        slug=slug,
        fb_dir=f"{fb_root}/{slug}/",
        fb_file=f"{fb_root}/{slug}/{slug}.epub",
        kavita_dir=f"{kavita_root}/{slug}",
    )


def slug_from_kavita_folder(folder_path: str, kavita_root: str) -> str | None:
    """The slug of a series folder directly under the Reports root, else None."""
    if not folder_path:
        return None
    folder = folder_path[:-1] if folder_path.endswith("/") else folder_path
    if posixpath.normpath(folder) != folder:
        return None
    parent, name = posixpath.split(folder)
    if parent != kavita_root.rstrip("/") or not name:
        return None
    try:
        _check(name)
    except InvalidSlug:
        return None
    return name


# Kavita 0.9.1.4 cuts a book's title at a volume number ("Report v2 Final" becomes
# series "Report"). Ported from MangaVolumeRegex in Kavita.Services/Scanner/Parser.cs
# (github.com/Kareadita/Kavita, commit d77d956), which ParseVolume uses for Book
# libraries. RemoveDuplicateVolumeIfExists is left out: it never decides whether
# a volume is found.
_NUMBER = r"\d+(\.\d)?"
_NUMBER_RANGE = _NUMBER + r"(-" + _NUMBER + r")?"
_VOLUME_PATTERNS = [
    r"(เล่ม|เล่มที่)(\s)?(\.?)(\s|_)?(?P<Volume>\d+(\-\d+)?(\.\d+)?)",
    r"(?P<Series>.*)(\b|_)(v|tome(\s|_)?|t)(?P<Volume>\d+-?\d+)(\s|_)",
    r"^(?P<Series>.+?)(\s*Chapter\s*\d+)?(\s|_|\-\s)+((Vol(ume)?|tome)\.?(\s|_)?)"
    r"(?P<Volume>\d+(\.\d+)?(\-\d+(\.\d+)?)?)(.+?|$)",
    r"(?P<Series>.*)(\b|_)(?!\[)v(?P<Volume>" + _NUMBER_RANGE + r")(?!\])(\b|_)",
    r"(?P<Series>.*)(\b|_)(vol\.? ?)(?P<Volume>\d+(\.\d)?(-\d+)?(\.\d)?)",
    r"(vol\.? ?)(?P<Volume>\d+(\.\d)?)",
    r"((volume|tome)\s)(?P<Volume>\d+(\.\d)?)",
    r"(?P<Series>.*)(\b|_)((S|T)(?P<Volume>\d+)(\b|_))",
    r"(vol_)(?P<Volume>\d+(\.\d)?)",
    r"第(?P<Volume>\d+)(卷|册)",
    r"(卷|册)(?P<Volume>\d+)",
    r"제?(?P<Volume>\d+(\.\d+)?)(권|장)",
    r"시즌(?P<Volume>\d+(\-\d+)?)",
    r"(?P<Volume>\d+(\-|~)?\d+?)시즌",
    r"시즌(?P<Volume>\d+(\-|~)?\d+?)",
    r"(?P<Volume>\d+(?:(\-)\d+)?)巻",
    r"Том(а?)(\.?)(\s|_)?(?P<Volume>\d+(?:(\-)\d+)?)",
    r"(\s|_)?(?P<Volume>\d+(?:(\-)\d+)?)(\s|_)Том(а?)",
]
_VOLUME_RES = [re.compile(p, re.IGNORECASE) for p in _VOLUME_PATTERNS]


def kavita_would_split(title: str) -> bool:
    return any(m.group("Volume") for r in _VOLUME_RES for m in r.finditer(title))
