"""Acceptance tests against the throwaway (make integration).

Needs `scripts/throwaway.sh up` and `scripts/throwaway.sh mcp`: Kavita 0.9.1 and
File Browser laid out like the VPS, and three kavita-mcp containers built from
this checkout (admin key, non-admin key, wrong key).
"""

import asyncio
import json
import subprocess
import uuid
from pathlib import Path

import httpx
import pytest
from mcp import Client

ENV_FILE = Path(__file__).parents[2] / ".throwaway" / "env"


def _env() -> dict[str, str]:
    if not ENV_FILE.exists():
        pytest.skip("no throwaway: run scripts/throwaway.sh up && scripts/throwaway.sh mcp")
    return dict(line.split("=", 1) for line in ENV_FILE.read_text().splitlines() if "=" in line)


@pytest.fixture
def env() -> dict[str, str]:
    return _env()


@pytest.fixture
def unique() -> str:
    return uuid.uuid4().hex[:6]


@pytest.fixture
async def kavita(env):
    async with httpx.AsyncClient(
        base_url=env["KAVITA_URL"], headers={"x-api-key": env["KAVITA_API_KEY"]}, timeout=30
    ) as client:
        yield client


async def call(url: str, tool: str, args: dict) -> tuple[bool, object]:
    """(is_error, parsed JSON result or the error text)."""
    async with Client(url) as client:
        result = await client.call_tool(tool, args)
    text = result.content[0].text if result.content else ""
    if result.is_error:
        return True, text
    return False, json.loads(text)


async def eventually(check, timeout: float = 20.0, interval: float = 1.0):
    """Await check() until it returns something truthy."""
    deadline = asyncio.get_running_loop().time() + timeout
    while True:
        value = await check()
        if value or asyncio.get_running_loop().time() >= deadline:
            return value
        await asyncio.sleep(interval)


def docker_exec(container: str, *command: str) -> subprocess.CompletedProcess:
    return subprocess.run(["docker", "exec", container, *command], capture_output=True, text=True)
