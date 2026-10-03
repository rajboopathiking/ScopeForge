"""Spine tests: handshake, ordering (small N), version fail-closed, unknown method.

The 10k-event soak + sub-second cancel run in tests/e2e/spine.test.ts (vitest).
Stdlib only: spawns the server over stdio, no third-party deps.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

SERVER = Path(__file__).resolve().parents[3] / "services/engine/src/scopeforge_engine/server.py"


def start(env=None):
    merged = dict(os.environ)
    if env:
        merged.update(env)
    return subprocess.Popen(
        [sys.executable, str(SERVER)],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True, bufsize=1, env=merged,
    )


def rpc(p, mid, method, params=None, proto="0.1.0"):
    assert p.stdin and p.stdout
    p.stdin.write(json.dumps({
        "jsonrpc": "2.0", "id": mid, "method": method, "params": params or {},
        "protocol_version": proto, "trace_id": f"t-py-{mid}",
        "ts": "2026-09-16T00:00:00Z"}) + "\n")
    p.stdin.flush()
    events = []
    while True:
        line = p.stdout.readline()
        assert line, "engine closed stdout unexpectedly"
        msg = json.loads(line)
        if msg.get("method") is not None and msg.get("id") is None:
            events.append(msg)  # notification/event
            continue
        return msg, events


def test_handshake_and_health():
    p = start()
    try:
        res, _ = rpc(p, 1, "initialize", {"protocol_version": "0.1.0", "client": "pytest"})
        assert res["result"]["protocol_version"] == "0.1.0"
        res, _ = rpc(p, 2, "health")
        assert res["result"]["ok"] is True
        rpc(p, 3, "shutdown")
    finally:
        p.kill()


def test_major_mismatch_fails_closed():
    p = start()
    try:
        res, _ = rpc(p, 1, "initialize", {"protocol_version": "99.0.0"}, proto="99.0.0")
        assert res["error"]["code"] == 1001
        assert "major" in res["error"]["message"].lower()
        rpc(p, 2, "shutdown", proto="0.1.0")
    finally:
        p.kill()


def test_ordered_events_small():
    p = start()
    try:
        rpc(p, 1, "initialize", {"protocol_version": "0.1.0"})
        res, events = rpc(p, 2, "event.subscribe", {"count": 200})
        assert res["result"]["received"] == 200
        seqs = [e["params"]["seq"] for e in events if e.get("method") == "token.delta"]
        assert len(seqs) == 200
        assert all(b > a for a, b in zip(seqs, seqs[1:]))
        rpc(p, 3, "shutdown")
    finally:
        p.kill()


def test_unknown_method():
    p = start()
    try:
        rpc(p, 1, "initialize", {"protocol_version": "0.1.0"})
        res, _ = rpc(p, 2, "nope.unknown")
        assert res["error"]["code"] == -32601
        rpc(p, 3, "shutdown")
    finally:
        p.kill()


def test_provider_registry_declared_no_secrets():
    p = start()
    try:
        rpc(p, 1, "initialize", {"protocol_version": "0.1.0"})
        res, _ = rpc(p, 2, "provider.list")
        providers = res["result"]["providers"]
        assert {"mock", "openai-compat", "deepseek", "anthropic-compat"} <= {pr["name"] for pr in providers}
        assert all(pr["builtin"] for pr in providers)
        res, _ = rpc(p, 3, "model.list", {"provider": "deepseek"})
        assert "deepseek-reasoner" in res["result"]["models"]
        res, _ = rpc(p, 4, "model.probe",
                     {"provider": "deepseek", "model": "deepseek-reasoner"})
        caps = res["result"]["capabilities"]
        assert caps["tool_calling"] is False and caps["reasoning"] is True
        assert res["result"]["verified_live"] is False
        res, _ = rpc(p, 5, "model.probe", {"provider": "nope", "model": "m"})
        assert res["error"]["code"] == -32602
        res, _ = rpc(p, 6, "model.probe",
                     {"provider": "mock", "model": "mock-turn", "live": True})
        assert res["result"]["verified_live"] is True
        rpc(p, 7, "shutdown")
    finally:
        p.kill()


def test_configured_provider_merges_with_ref_only(tmp_path):
    (tmp_path / "providers.toml").write_text(
        '[providers.work]\ntype = "deepseek"\n'
        'base_url = "https://api.deepseek.com"\ncredential = "env:DEEPSEEK_API_KEY"\n')
    p = start({"SCOPEFORGE_HOME": str(tmp_path)})
    try:
        rpc(p, 1, "initialize", {"protocol_version": "0.1.0"})
        res, _ = rpc(p, 2, "provider.list")
        work = [pr for pr in res["result"]["providers"] if pr["name"] == "work"]
        assert len(work) == 1 and work[0]["builtin"] is False
        assert work[0]["credential"] == "env:DEEPSEEK_API_KEY"
        res, _ = rpc(p, 3, "model.list", {"provider": "work"})
        assert "deepseek-chat" in res["result"]["models"]
        res, _ = rpc(p, 4, "model.probe", {"provider": "work", "model": "deepseek-chat"})
        assert res["result"]["type"] == "deepseek"
        assert res["result"]["credential"] == "env:DEEPSEEK_API_KEY"
        rpc(p, 5, "shutdown")
    finally:
        p.kill()


def test_run_lifecycle(tmp_path):
    p = start({"SCOPEFORGE_HOME": str(tmp_path)})
    try:
        rpc(p, 1, "initialize", {"protocol_version": "0.1.0"})
        res, _ = rpc(p, 2, "run.create", {"mode": "plan"})
        assert res["result"]["status"] == "draft"
        rid = res["result"]["run_id"]
        res, _ = rpc(p, 3, "run.status", {"run_id": rid})
        assert res["result"]["mode"] == "plan"
        assert res["result"]["budget"]["requests_limit"] is None  # unknown, never unlimited
        res, _ = rpc(p, 4, "run.list")
        assert [r["run_id"] for r in res["result"]["runs"]] == [rid]
        res, _ = rpc(p, 5, "run.status", {"run_id": "run_9999"})
        assert res["error"]["code"] == -32602
        res, _ = rpc(p, 6, "run.close", {"run_id": rid})
        assert res["result"] == {"run_id": rid, "closed": True}
        res, _ = rpc(p, 7, "run.status", {"run_id": rid})
        assert res["result"]["status"] == "closed"
        rpc(p, 8, "shutdown")
    finally:
        p.kill()


def test_snapshots_survive_restart(tmp_path):
    home = str(tmp_path)
    p = start({"SCOPEFORGE_HOME": home})
    try:
        rpc(p, 1, "initialize", {"protocol_version": "0.1.0"})
        res, _ = rpc(p, 2, "run.create", {"mode": "artifacts"})
        rid = res["result"]["run_id"]
        res, events = rpc(p, 3, "event.subscribe", {"run_id": rid, "count": 25})
        assert res["result"]["received"] == 25
        assert len([e for e in events if e.get("method") == "token.delta"]) == 25
        rpc(p, 4, "shutdown")
        p.wait(timeout=10)
    finally:
        try:
            p.kill()
        except OSError:
            pass
    snap = tmp_path / "runs" / rid / "run-state.json"
    assert snap.is_file()
    assert json.loads(snap.read_text())["events"] == 25
    # Fresh engine process reloads the registry and continues numbering.
    p2 = start({"SCOPEFORGE_HOME": home})
    try:
        rpc(p2, 1, "initialize", {"protocol_version": "0.1.0"})
        res, _ = rpc(p2, 2, "run.list")
        assert res["result"]["runs"][0]["run_id"] == rid
        assert res["result"]["runs"][0]["events"] == 25
        res, _ = rpc(p2, 3, "run.create", {"mode": "plan"})
        assert res["result"]["run_id"] != rid
        rpc(p2, 4, "shutdown")
    finally:
        p2.kill()
