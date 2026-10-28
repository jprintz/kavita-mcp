"""Compact, LLM-friendly rows instead of Kavita's DTOs."""


def read_status(pages: int, pages_read: int) -> str:
    if pages_read <= 0:
        return "unread"
    if pages_read >= pages:
        return "read"
    return "in_progress"


def progress_pct(pages: int, pages_read: int) -> int:
    if pages <= 0:
        return 0
    return min(100, round(100 * pages_read / pages))


def author_of(metadata: dict) -> str | None:
    names = [w.get("name") for w in metadata.get("writers") or [] if w.get("name")]
    return ", ".join(names) or None


def series_row(series: dict, *, author: str | None) -> dict:
    pages = series.get("pages") or 0
    pages_read = series.get("pagesRead") or 0
    return {
        "series_id": series["id"],
        "title": series.get("name"),
        "author": author,
        "library": series.get("libraryName"),
        "added": (series.get("created") or "")[:10] or None,
        "word_count": series.get("wordCount"),
        "pages": pages,
        "read_status": read_status(pages, pages_read),
        "progress_pct": progress_pct(pages, pages_read),
    }


def series_url(public_url: str | None, library_id: int, series_id: int) -> str | None:
    if not public_url:
        return None
    return f"{public_url}/library/{library_id}/series/{series_id}"
