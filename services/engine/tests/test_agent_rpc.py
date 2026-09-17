"""Agent RPCs over stdio (stdlib-only; mock provider needs no network)."""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

SERVER = Path(__file__).resolve().parents[3] / "services/engine/src/scopeforge_engine/server.py"

DESIGN = {
    "check_id": "T02.1", "asset": "https://authorized.example",
    "hypothesis": "h", "expected_rule": "r", "baseline": "b",
    "changed_variable": "v", "observable_result": "o",
    "stopping_point": "s", "permission_ref": "P-1",
}


def start(env=None):
    import os

    merged = dict(os.environ)
    if env:
        merged.update(env)
    return subprocess.Popen(
        [sys.executable, str(SERVER)],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True, bufsize=1, env=merged,
    )


def rpc(p, mid, method, params=None):
    assert p.stdin and p.stdout
    p.stdin.write(json.dumps({
        "jsonrpc": "2.0", "id": mid, "method": method, "params": params or {},
        "protocol_version": "0.1.0", "trace_id": f"t-agent-{mid}",
        "ts": "2026-09-17T00:00:00Z"}) + "\n")
    p.stdin.flush()
    while True:
        line = p.stdout.readline()
        assert line, "engine closed stdout unexpectedly"
        msg = json.loads(line)
        if msg.get("method") is None or msg.get("id") is not None:
            return msg


def session(tmp_path):
    p = start({"SCOPEFORGE_HOME": str(tmp_path)})
    rpc(p, 1, "initialize", {"protocol_version": "0.1.0"})
    res = rpc(p, 2, "run.create", {"mode": "artifacts"})
    return p, res["result"]["run_id"]


def test_chat_submit_mock_structure_and_coach_coverage(tmp_path):
    p, rid = session(tmp_path)
    try:
        res = rpc(p, 10, "chat.submit", {"run_id": rid, "message": "hello"})
        out = res["result"]
        assert out["role"] == "research_assistant" and out["stored"] is False
        assert any("parseable" in g for g in out["gaps"])  # empty mock script
        res = rpc(p, 11, "case.create", {"run_id": rid, "case": DESIGN})
        cid = res["result"]["case"]["case_id"]
        res = rpc(p, 12, "coach.score", {"run_id": rid, "case_id": cid})
        assert len(res["result"]["scores"]) == 10
        assert len(res["result"]["questions"]) == 7
        res = rpc(p, 13, "coverage.report", {"run_id": rid})
        assert len(res["result"]["categories"]) == 12
        res = rpc(p, 14, "case.execute", {"run_id": rid, "case_id": cid})
        assert "proposal" in res["result"] and "decision" in res["result"]
        rpc(p, 15, "shutdown")
    finally:
        p.kill()


def test_report_build_refuses_ungated_case(tmp_path):
    p, rid = session(tmp_path)
    try:
        res = rpc(p, 10, "case.create", {"run_id": rid, "case": DESIGN})
        cid = res["result"]["case"]["case_id"]
        res = rpc(p, 11, "artifact.import",
                  {"run_id": rid, "filename": "n.txt", "content_b64": "e30=",
                   "source_type": "note"})
        eid = res["result"]["evidence_id"]
        rpc(p, 12, "case.update",
            {"run_id": rid, "case_id": cid,
             "fields": {"result": "lead", "observed_behavior": "scanner hit",
                        "evidence_refs": [eid]}})
        finding = {k: f"{k} text" for k in (
            "title", "asset_scope", "preconditions", "summary", "rule", "expected",
            "actual", "demonstrated_impact", "remediation", "limits")}
        finding.update({"reproduction": ["one"], "evidence": [eid], "case_ref": cid})
        res = rpc(p, 13, "report.build", {"run_id": rid, "finding": finding})
        assert res["error"]["code"] == -32602
        assert "gaps" in res["error"]["message"]  # unvalidated lead cannot render
        res = rpc(p, 14, "case.update",
                  {"run_id": rid, "case_id": cid,
                   "fields": {"result": "validated_finding"}})
        assert res["error"]["code"] == -32602  # gate refuses promotion too
        rpc(p, 15, "shutdown")
    finally:
        p.kill()
