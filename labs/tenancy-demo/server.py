#!/usr/bin/env python3
"""Tenancy-demo lab server (Phase 7). Researcher-owned fixtures only.

Two tenants, two accounts, one project. Binds 127.0.0.1. Prints READY port=N.
Stdlib only. State resets via POST /reset.
"""
from __future__ import annotations

import argparse
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

OWNERS = {"proj_A": "tenant-a"}
ACTORS = {"account-a": "tenant-a", "account-b": "tenant-b"}
FILES = {"proj_A": ["plan.pdf"]}
JOBS: dict = {}
FLAKY = {"n": 0}


class Handler(BaseHTTPRequestHandler):
    server_version = "TenancyDemo/0.1.0"

    def log_message(self, *args):
        pass

    def _json(self, status: int, obj: dict, extra: list | None = None) -> None:
        body = json.dumps(obj).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        for key, value in extra or []:
            self.send_header(key, value)
        self.end_headers()
        self.wfile.write(body)

    def _actor(self) -> str | None:
        return self.headers.get("X-Actor")

    def do_GET(self):
        url = urlsplit(self.path)
        if url.path == "/export":
            project = parse_qs(url.query).get("project", [""])[0]
            tenant = ACTORS.get(self._actor() or "")
            if OWNERS.get(project) == tenant and tenant:
                self._json(200, {"project": project, "tenant": tenant,
                                 "files": FILES.get(project, [])})
            else:
                self._json(403, {"error": "forbidden", "code": "NOT_MEMBER"})
        elif url.path == "/go-in":
            self.send_response(302)
            self.send_header("Location", "/export?project=proj_A")
            self.end_headers()
        elif url.path == "/go-out":
            self.send_response(302)
            self.send_header("Location", "https://example.invalid/")
            self.end_headers()
        elif url.path == "/echo-headers":
            self._json(200, {
                "authorization_present": self.headers.get("Authorization") is not None,
                "actor": self._actor(),
                "n_headers": len(self.headers),
            })
        else:
            self._json(404, {"error": "not found"})

    def do_POST(self):
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b""
        url = urlsplit(self.path)
        if url.path == "/reset":
            JOBS.clear()
            FLAKY["n"] = 0
            self._json(200, {"reset": True})
        elif url.path == "/flaky-post":
            FLAKY["n"] += 1
            if FLAKY["n"] == 1:
                self._json(500, {"error": "boom"})
            else:
                self._json(200, {"n": FLAKY["n"]})
        elif url.path == "/export":
            try:
                body = json.loads(raw or b"{}")
            except ValueError:
                self._json(400, {"error": "bad json"})
                return
            tenant = ACTORS.get(self._actor() or "")
            project = body.get("project", "")
            if OWNERS.get(project) == tenant and tenant:
                job = f"job_{len(JOBS) + 1}"
                JOBS[job] = {"project": project, "by": self._actor()}
                self._json(201, {"job": job})
            else:
                self._json(403, {"error": "forbidden", "code": "NOT_MEMBER"})
        else:
            self._json(404, {"error": "not found"})


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=0)
    args = ap.parse_args()
    server = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    print(f"READY port={server.server_address[1]}", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
