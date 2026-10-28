import os
import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def pandoc_bin() -> str:
    found = os.environ.get("PANDOC") or shutil.which("pandoc")
    if not found:
        if os.environ.get("REQUIRE_PANDOC"):
            pytest.fail("pandoc is required (REQUIRE_PANDOC is set) but not found")
        pytest.skip("pandoc not found (set PANDOC or put it on PATH)")
    return found


def _epubcheck_command() -> list[str] | None:
    # EPUBCHECK: an executable taking the EPUB path (e.g. a wrapper that runs Java
    # in a container). Otherwise java -jar $EPUBCHECK_JAR.
    if cmd := os.environ.get("EPUBCHECK"):
        return [cmd]
    jar = os.environ.get("EPUBCHECK_JAR")
    if jar and shutil.which("java"):
        probe = subprocess.run(["java", "-version"], capture_output=True)
        if probe.returncode == 0:
            return ["java", "-jar", jar]
    return None


@pytest.fixture
def epubcheck():
    command = _epubcheck_command()
    if command is None:
        if os.environ.get("REQUIRE_EPUBCHECK"):
            pytest.fail("epubcheck is required (REQUIRE_EPUBCHECK is set) but not configured")
        pytest.skip("epubcheck not configured (EPUBCHECK or EPUBCHECK_JAR with java)")

    def check(epub: bytes) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "book.epub"
            path.write_bytes(epub)
            result = subprocess.run([*command, str(path)], capture_output=True, text=True)
        assert result.returncode == 0, result.stdout + result.stderr

    return check
