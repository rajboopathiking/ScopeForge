"""Phase-4 record layer over stdio RPC (stdlib-only; runs on bare interpreters)."""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

SERVER = Path(__file__).resolve().parents[3] / "services/engine/src/scopeforge_engine/server.py"

VALID_CASE = {
    "check_id": "T02.1", "asset": "https://authorized.example",
    "hypothesis": "member B exports A's project", "expected_rule": "owners only",
    "baseline": "A exports own fixture", "changed_variable": "act as B",
    "observable_result": "403, no data", "stopping_point": "one attempt",
    "permission_ref": "P-1",
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
        "protocol_version": "0.1.0", "trace_id": f"t-rpc-{mid}",
        "ts": "2026-09-16T00:00:00Z"}) + "\n")
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


def test_case_rpc_lifecycle(tmp_path):
    p, rid = session(tmp_path)
    try:
        res = rpc(p, 10, "case.create", {"run_id": rid, "case": {"check_id": "x"}})
        assert res["error"]["code"] == -32602
        res = rpc(p, 11, "case.create", {"run_id": rid, "case": VALID_CASE})
        assert res["result"]["case"]["case_id"] == "C-0001"
        res = rpc(p, 12, "case.list", {"run_id": rid})
        assert len(res["result"]["cases"]) == 1
        res = rpc(p, 13, "case.show", {"run_id": rid, "case_id": "C-0001"})
        assert res["result"]["case"]["check_id"] == "T02.1"
        res = rpc(p, 14, "case.update",
                  {"run_id": rid, "case_id": "C-0001", "fields": {"result": "lead"}})
        assert res["error"]["code"] == -32602  # executed results need evidence
        res = rpc(p, 15, "artifact.import",
                  {"run_id": rid, "filename": "note.txt", "content_b64": "aGk=",
                   "source_type": "note", "mime": "text/plain"})
        eid = res["result"]["evidence_id"]
        assert len(res["result"]["sha256"]) == 64
        res = rpc(p, 16, "case.update",
                  {"run_id": rid, "case_id": "C-0001",
                   "fields": {"result": "lead", "evidence_refs": [eid]}})
        assert res["result"]["case"]["observed_at"]
        rpc(p, 17, "shutdown")
    finally:
        p.kill()


def test_evidence_coverage_report_export_rebuild(tmp_path):
    p, rid = session(tmp_path)
    try:
        (tmp_path / "in.txt").write_text("hello artifact")
        res = rpc(p, 20, "artifact.import", {"run_id": rid, "path": str(tmp_path / "in.txt")})
        eid = res["result"]["evidence_id"]
        res = rpc(p, 21, "evidence.list", {"run_id": rid})
        assert [e["evidence_id"] for e in res["result"]["evidence"]] == [eid]
        res = rpc(p, 22, "evidence.show", {"run_id": rid, "evidence_id": eid, "preview": True})
        assert res["result"]["preview"] == "hello artifact"
        res = rpc(p, 23, "evidence.redact", {"run_id": rid, "evidence_id": eid})
        assert res["result"]["derived"]["derived_from"] == eid
        res = rpc(p, 24, "coverage.get", {"run_id": rid})
        assert len(res["result"]["coverage"]["categories"]) == 12
        cats = res["result"]["coverage"]["categories"]
        cats[1]["applicability"] = "applicable"
        res = rpc(p, 25, "coverage.set", {"run_id": rid, "categories": cats})
        assert res["result"]["coverage"]["categories"][1]["applicability"] == "applicable"
        res = rpc(p, 26, "report.build", {"run_id": rid, "finding": {"title": "t"}})
        assert "missing" in res["error"]["message"]
        finding = {k: f"{k} text" for k in (
            "title", "asset_scope", "preconditions", "summary", "rule", "expected",
            "actual", "demonstrated_impact", "remediation", "limits")}
        finding.update({"reproduction": ["one"], "evidence": [eid]})
        res = rpc(p, 27, "report.build", {"run_id": rid, "finding": finding})
        assert res["result"]["finding_id"] == "F-0001"
        res = rpc(p, 28, "export.create", {"run_id": rid})
        assert Path(res["result"]["path"]).is_file()
        res = rpc(p, 29, "run.rebuild", {})
        assert res["result"]["counts"] == {"runs": 1, "cases": 0, "evidence": 2}
        res = rpc(p, 30, "run.close", {"run_id": rid})
        assert res["result"] == {"run_id": rid, "closed": True}
        assert (tmp_path / "runs" / rid / "summary.md").is_file()
        rpc(p, 31, "shutdown")
    finally:
        p.kill()


def test_kill9_loses_no_completed_work(tmp_path):
    p, rid = session(tmp_path)
    rpc(p, 10, "case.create", {"run_id": rid, "case": VALID_CASE})
    rpc(p, 11, "artifact.import", {"run_id": rid, "filename": "n.txt",
                                   "content_b64": "e30=", "source_type": "note"})
    rpc(p, 12, "event.subscribe", {"run_id": rid, "count": 600})
    p.kill()  # SIGKILL equivalent: no shutdown, no flush beyond checkpoints
    p.wait(timeout=10)
    p2 = start({"SCOPEFORGE_HOME": str(tmp_path)})
    try:
        rpc(p2, 1, "initialize", {"protocol_version": "0.1.0"})
        res = rpc(p2, 2, "run.list")
        assert res["result"]["runs"][0]["run_id"] == rid
        res = rpc(p2, 3, "case.list", {"run_id": rid})
        assert res["result"]["cases"][0]["case_id"] == "C-0001"
        res = rpc(p2, 4, "evidence.list", {"run_id": rid})
        assert len(res["result"]["evidence"]) == 1
        res = rpc(p2, 5, "run.rebuild", {})
        assert res["result"]["counts"]["runs"] == 1
        rpc(p2, 6, "shutdown")
    finally:
        p2.kill()
