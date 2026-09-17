"""Live provider probes (uv env only): localhost HTTP server + configured
provider proves the authenticated reachability path with zero external network.
"""
from __future__ import annotations

import json
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

httpx = pytest.importorskip("httpx")

SERVER = Path(__file__).resolve().parents[3] / "services/engine/src/scopeforge_engine/server.py"
SEEN_HEADERS: list[dict] = []


class ModelsHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/models":
            SEEN_HEADERS.append(dict(self.headers))
            body = b'{"data":[{"id":"test-model"}],"object":"list"}'
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, *args):
        pass


@pytest.fixture()
def local_api(tmp_path, monkeypatch):
    SEEN_HEADERS.clear()
    server = ThreadingHTTPServer(("127.0.0.1", 0), ModelsHandler)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    monkeypatch.setenv("SF_LIVE_TEST_KEY", "dummy-local-key")
    (tmp_path / "providers.toml").write_text(
        '[providers.localtest]\ntype = "openai-compat"\n'
        f'base_url = "http://127.0.0.1:{port}"\ncredential = "env:SF_LIVE_TEST_KEY"\n')
    yield tmp_path
    server.shutdown()


def start(home):
    import os

    env = dict(os.environ, SCOPEFORGE_HOME=str(home))
    return subprocess.Popen(
        [sys.executable, str(SERVER)],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True, bufsize=1, env=env,
    )


def rpc(p, mid, method, params=None):
    assert p.stdin and p.stdout
    p.stdin.write(json.dumps({
        "jsonrpc": "2.0", "id": mid, "method": method, "params": params or {},
        "protocol_version": "0.1.0", "trace_id": f"t-live-{mid}",
        "ts": "2026-09-16T00:00:00Z"}) + "\n")
    p.stdin.flush()
    while True:
        line = p.stdout.readline()
        assert line, "engine closed stdout"
        msg = json.loads(line)
        if msg.get("method") is None or msg.get("id") is not None:
            return msg


def test_live_probe_against_localhost(local_api):
    p = start(local_api)
    try:
        rpc(p, 1, "initialize", {"protocol_version": "0.1.0"})
        res = rpc(p, 2, "model.probe", {"provider": "localtest", "model": "test-model",
                        "live": True})
        assert res["result"]["verified_live"] is True
        assert "reachable" in res["result"]["reachability"]
        assert SEEN_HEADERS and SEEN_HEADERS[0].get("Authorization") == "Bearer dummy-local-key"
        rpc(p, 3, "shutdown")
    finally:
        p.kill()


def test_live_probe_without_credential_fails_before_network(local_api):
    p = start(local_api)
    try:
        rpc(p, 1, "initialize", {"protocol_version": "0.1.0"})
        before = len(SEEN_HEADERS)
        res = rpc(p, 2, "model.probe", {"provider": "deepseek", "model": "deepseek-chat",
                        "live": True})
        assert res["result"]["verified_live"] is False
        assert "credential" in res["result"]["reachability"]
        assert len(SEEN_HEADERS) == before  # no HTTP attempted
        rpc(p, 3, "shutdown")
    finally:
        p.kill()
