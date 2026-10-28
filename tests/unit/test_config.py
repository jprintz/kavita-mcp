import pytest

from kavita_mcp.config import Settings
from kavita_mcp.errors import ConfigError

KEY = "k" * 20


def test_defaults():
    s = Settings.from_env({"KAVITA_API_KEY": KEY})
    assert s.kavita_url == "http://kavita:5000"
    assert s.kavita_api_key == KEY
    assert s.kavita_reports_path == "/reports"
    assert s.filebrowser_url == "http://kavita-upload:8080"
    assert s.filebrowser_reports_dir == "/Reports"
    assert s.scan_timeout_seconds == 60
    assert s.max_markdown_bytes == 2 * 1024 * 1024
    assert s.max_image_bytes == 1024 * 1024
    assert (s.transport, s.host, s.port) == ("streamable-http", "0.0.0.0", 8000)
    assert s.kavita_public_url is None


def test_missing_key_raises():
    with pytest.raises(ConfigError, match="KAVITA_API_KEY"):
        Settings.from_env({})


def test_public_url_trailing_slash_dropped():
    s = Settings.from_env({"KAVITA_API_KEY": KEY, "KAVITA_PUBLIC_URL": "https://kavita.example/"})
    assert s.kavita_public_url == "https://kavita.example"


def test_overrides():
    s = Settings.from_env(
        {
            "KAVITA_API_KEY": KEY,
            "KAVITA_URL": "http://127.0.0.1:5055/",
            "SCAN_TIMEOUT_SECONDS": "5",
            "MAX_MARKDOWN_BYTES": "100",
            "KAVITA_MCP_PORT": "18000",
            "KAVITA_MCP_TRANSPORT": "stdio",
        }
    )
    assert s.kavita_url == "http://127.0.0.1:5055"
    assert s.scan_timeout_seconds == 5
    assert s.max_markdown_bytes == 100
    assert s.port == 18000
    assert s.transport == "stdio"


def test_bad_number_raises():
    with pytest.raises(ConfigError, match="SCAN_TIMEOUT_SECONDS"):
        Settings.from_env({"KAVITA_API_KEY": KEY, "SCAN_TIMEOUT_SECONDS": "soon"})
