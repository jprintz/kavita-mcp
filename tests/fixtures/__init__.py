"""Kavita responses recorded from a real 0.9.1.4 (scripts/record_fixtures.py)."""

import json
from collections.abc import Callable
from pathlib import Path

import httpx

KAVITA = Path(__file__).parent / "kavita"


def load(name: str) -> dict:
    return json.loads((KAVITA / f"{name}.json").read_text())


def response(name: str) -> httpx.Response:
    record = load(name)
    return httpx.Response(record["status"], headers=record["headers"], json=record["body"])


Route = str | Callable[[httpx.Request], httpx.Response]


class FixtureTransport(httpx.MockTransport):
    """Serves recorded fixtures by (method, path); remembers every request."""

    def __init__(self, routes: dict[tuple[str, str], Route]):
        self.routes = routes
        self.requests: list[httpx.Request] = []
        super().__init__(self._handle)

    def _handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        route = self.routes.get((request.method, request.url.path))
        if route is None:
            return httpx.Response(404, text=f"no fixture for {request.method} {request.url.path}")
        if isinstance(route, str):
            return response(route)
        return route(request)

    def last(self, method: str, path: str) -> httpx.Request:
        return [r for r in self.requests if r.method == method and r.url.path == path][-1]

    def bodies(self, method: str, path: str) -> list:
        return [
            json.loads(r.content) if r.content else None
            for r in self.requests
            if r.method == method and r.url.path == path
        ]
