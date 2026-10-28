"""Settings from environment variables."""

import os
from collections.abc import Mapping
from dataclasses import dataclass

from kavita_mcp.errors import ConfigError

MIB = 1024 * 1024


@dataclass(frozen=True)
class Settings:
    kavita_url: str
    kavita_api_key: str
    kavita_public_url: str | None
    # The Reports library's folder as Kavita sees it, and the same folder as
    # File Browser (the upload service) sees it.
    kavita_reports_path: str
    filebrowser_url: str
    filebrowser_reports_dir: str
    scan_timeout_seconds: float
    max_markdown_bytes: int
    max_image_bytes: int
    transport: str
    host: str
    port: int

    @classmethod
    def from_env(cls, env: Mapping[str, str] = os.environ) -> "Settings":
        key = env.get("KAVITA_API_KEY", "").strip()
        if not key:
            raise ConfigError("KAVITA_API_KEY is not set: give kavita-mcp a Kavita auth key")
        public_url = env.get("KAVITA_PUBLIC_URL", "").strip().rstrip("/") or None
        return cls(
            kavita_url=env.get("KAVITA_URL", "http://kavita:5000").rstrip("/"),
            kavita_api_key=key,
            kavita_public_url=public_url,
            kavita_reports_path=env.get("KAVITA_REPORTS_PATH", "/reports").rstrip("/"),
            filebrowser_url=env.get("FILEBROWSER_URL", "http://kavita-upload:8080").rstrip("/"),
            filebrowser_reports_dir=env.get("FILEBROWSER_REPORTS_DIR", "/Reports").rstrip("/"),
            scan_timeout_seconds=_number(env, "SCAN_TIMEOUT_SECONDS", 60, float),
            max_markdown_bytes=_number(env, "MAX_MARKDOWN_BYTES", 2 * MIB, int),
            max_image_bytes=_number(env, "MAX_IMAGE_BYTES", MIB, int),
            transport=env.get("KAVITA_MCP_TRANSPORT", "streamable-http"),
            host=env.get("KAVITA_MCP_HOST", "0.0.0.0"),
            port=_number(env, "KAVITA_MCP_PORT", 8000, int),
        )


def _number(env: Mapping[str, str], name: str, default, kind):
    raw = env.get(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        return kind(raw)
    except ValueError:
        raise ConfigError(f"{name} must be a number, got {raw!r}") from None
