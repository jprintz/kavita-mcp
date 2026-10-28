"""In-memory stand-ins for Kavita and File Browser, for tool-level tests."""

from kavita_mcp.kavita import KavitaNotFound

REPORTS_ID = 2
BOOKS_ID = 1


class FakeFiles:
    """File Browser: path -> bytes."""

    def __init__(self):
        self.files: dict[str, bytes] = {}

    async def exists(self, path: str) -> bool:
        return path in self.files

    async def upload(self, path: str, data: bytes) -> None:
        self.files[path] = data

    async def download(self, path: str) -> bytes:
        return self.files[path]

    async def delete(self, path: str) -> bool:
        hit = [p for p in self.files if p.startswith(path)]
        for p in hit:
            del self.files[p]
        return bool(hit)

    async def list_dir(self, path: str) -> list[str]:
        return sorted({p[len(path) :].split("/")[0] for p in self.files if p.startswith(path)})


class FakeKavita:
    """Kavita with a Books and a Reports library; a scan mirrors FakeFiles."""

    def __init__(self, files: FakeFiles, *, imports: bool = True):
        self.files = files
        self.imports = imports  # False: scans never pick anything up
        self.scan_error: Exception | None = None
        self.scans = 0
        self.series_by_id: dict[int, dict] = {}
        self.added: list[tuple[str, int]] = []
        self.removed: list[tuple[str, int]] = []
        self.next_id = 100

    def put(self, folder: str, *, library_id: int = REPORTS_ID, name: str = "x") -> int:
        self.next_id += 1
        self.series_by_id[self.next_id] = {
            "id": self.next_id,
            "name": name,
            "folderPath": folder,
            "libraryId": library_id,
            "libraryName": "Reports" if library_id == REPORTS_ID else "Books",
            "pages": 3,
            "pagesRead": 0,
            "wordCount": 100,
            "created": "2026-10-28T10:00:00",
        }
        return self.next_id

    async def libraries(self) -> list[dict]:
        return [
            {"id": BOOKS_ID, "name": "Books", "folders": ["/books"]},
            {"id": REPORTS_ID, "name": "Reports", "folders": ["/reports/"]},
        ]

    async def series(self, series_id: int) -> dict:
        if series_id not in self.series_by_id:
            raise KavitaNotFound(f"Kavita has no such item (/api/Series/{series_id})")
        return self.series_by_id[series_id]

    async def series_by_folder(self, folder: str) -> dict | None:
        return next((s for s in self.series_by_id.values() if s["folderPath"] == folder), None)

    async def scan_library(self, library_id: int) -> None:
        if self.scan_error:
            raise self.scan_error
        self.scans += 1
        if not self.imports:
            return
        folders = {"/reports/" + p.split("/")[2] for p in self.files.files}
        for sid, s in list(self.series_by_id.items()):
            if s["libraryId"] == REPORTS_ID and s["folderPath"] not in folders:
                del self.series_by_id[sid]
        for folder in folders:
            if await self.series_by_folder(folder) is None:
                self.put(folder)

    async def add_to_collection(self, series_id: int, title: str) -> int:
        self.added.append((title, series_id))
        return 1

    async def remove_from_collection(self, series_id: int, title: str) -> None:
        self.removed.append((title, series_id))
