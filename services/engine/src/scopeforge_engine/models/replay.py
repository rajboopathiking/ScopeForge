"""Record/replay contract harness (Phase 3).

Fixtures are ordered JSONL exchanges, sanitized at record time. Replay serves
them back through a real httpx transport so adapters run their full HTTP path
offline. No live credentials are needed to run the conformance suite.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx

from .sanitize import assert_no_secrets, sanitize_headers, sanitize_json


@dataclass
class Exchange:
    request_method: str
    request_path: str
    request_json: Any = None
    status: int = 200
    headers: dict[str, str] = field(default_factory=dict)
    body: str = ""  # raw SSE text or JSON document


def exchange_to_dict(e: Exchange) -> dict[str, Any]:
    return {
        "request": {"method": e.request_method, "path": e.request_path, "json": e.request_json},
        "response": {"status": e.status, "headers": e.headers, "body": e.body},
    }


def exchange_from_dict(d: dict[str, Any]) -> Exchange:
    req, resp = d["request"], d["response"]
    return Exchange(request_method=req["method"], request_path=req["path"],
                    request_json=req.get("json"), status=resp.get("status", 200),
                    headers=resp.get("headers", {}), body=resp.get("body", ""))


def load_exchanges(path: str | Path) -> list[Exchange]:
    exchanges: list[Exchange] = []
    for line in Path(path).read_text().splitlines():
        if line.strip():
            exchanges.append(exchange_from_dict(json.loads(line)))
    return exchanges


def sanitize_exchange(e: Exchange) -> Exchange:
    return Exchange(
        request_method=e.request_method, request_path=e.request_path,
        request_json=sanitize_json(e.request_json), status=e.status,
        headers=sanitize_headers(e.headers), body=sanitize_body(e.body),
    )


def sanitize_body(body: str) -> str:
    from .sanitize import sanitize_text

    return sanitize_text(body)


def scan_fixture_file(path: str | Path) -> None:
    """Fail closed if a checked-in fixture contains secret shapes or secret headers."""
    text = Path(path).read_text()
    assert_no_secrets(text, where=str(path))


class ReplayTransport(httpx.AsyncBaseTransport):
    """Serve scripted exchanges in order; assert method + path suffix per request."""

    def __init__(self, exchanges: list[Exchange]) -> None:
        self._queue = list(exchanges)
        self.requests: list[httpx.Request] = []
        self.served = 0

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if not self._queue:
            return httpx.Response(500, json={"error": "replay exhausted"}, request=request)
        expected = self._queue.pop(0)
        assert request.method == expected.request_method, (
            f"replay: expected {expected.request_method}, got {request.method}")
        assert request.url.path.endswith(expected.request_path), (
            f"replay: expected path *{expected.request_path}, got {request.url.path}")
        self.served += 1
        return httpx.Response(expected.status, headers=expected.headers,
                              content=expected.body.encode(), request=request)

    @property
    def exhausted(self) -> bool:
        return not self._queue
