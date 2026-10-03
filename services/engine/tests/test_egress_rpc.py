"""Live RPC flows against the lab (needs httpx; uv env).

Proves the Phase-7 exits end to end: gated execution, approvals, experiments,
budget exhaustion with resumable closeout — all through stdio RPC.
"""
import json
import subprocess
import sys
from pathlib import Path

import pytest

httpx = pytest.importorskip("httpx")

from scopeforge_engine.labs import LabManager, find_labs_dir

SERVER = Path(__file__).resolve().parents[3] / "services/engine/src/scopeforge_engine/server.py"
LABS = find_labs_dir()


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
        "ts": "2026-09-17T00:00:00Z"}) + "\n")
    p.stdin.flush()
    while True:
        line = p.stdout.readline()
        assert line, "engine closed stdout"
        msg = json.loads(line)
        if msg.get("method") is None or msg.get("id") is not None:
            return msg


def live_policy(port):
    return {
        "program_name": "lab run", "policy_source": "lab", "mode": "live",
        "include": [{"host": "127.0.0.1", "port": port, "allow_private": True}],
        "methods_allow": ["GET", "HEAD", "POST"],
        "budgets": {"requests_total": 50, "max_concurrency": 1},
    }


@pytest.fixture()
def live_run(tmp_path):
    mgr = LabManager()
    lab = mgr.launch("tenancy-demo", LABS)
    port = int(lab["url"].rsplit(":", 1)[1])
    p = start(tmp_path)
    rpc(p, 1, "initialize", {"protocol_version": "0.1.0"})
    rid = rpc(p, 2, "run.create", {"mode": "live"})["result"]["run_id"]
    rpc(p, 3, "policy.import", {"run_id": rid, "policy": live_policy(port)})
    yield p, rid, lab["url"]
    try:
        rpc(p, 90, "shutdown")
    except (OSError, ValueError, AssertionError):
        pass
    p.kill()
    mgr.stop_all()


def test_tool_execute_get_and_preview_for_post(live_run):
    p, rid, url = live_run
    get = {"kind": "http.request", "method": "GET",
           "url": f"{url}/export?project=proj_A", "actor_ref": "account-b"}
    res = rpc(p, 10, "tool.execute", {"run_id": rid, "action": get})["result"]
    assert res["status"] == 403 and res["evidence_ids"]
    ev = rpc(p, 11, "evidence.show",
             {"run_id": rid, "evidence_id": res["evidence_ids"][0],
              "preview": True})["result"]
    assert "403" in ev["preview"]
    post = {"kind": "http.request", "method": "POST", "url": f"{url}/export",
            "actor_ref": "account-a", "body": '{"project": "proj_A"}'}
    preview = rpc(p, 12, "tool.execute", {"run_id": rid, "action": post})["result"]
    assert preview["sent"] is False and preview["approval_required"] is True
    scope = preview["approval_preview"]["scope"]
    rpc(p, 13, "approval.respond",
        {"run_id": rid, "scope": scope, "approved": True, "kind": "one-shot"})
    done = rpc(p, 14, "tool.execute", {"run_id": rid, "action": post})["result"]
    assert done["status"] == 201 and done["evidence_ids"]
    again = rpc(p, 15, "tool.execute", {"run_id": rid, "action": post})["result"]
    assert again["approval_required"] is True  # one-shot spent


def test_experiment_compare_one_variable(live_run):
    p, rid, url = live_run
    res = rpc(p, 20, "case.create", {"run_id": rid, "case": {
        "check_id": "T02.1", "asset": url, "hypothesis": "B cannot export A's project",
        "expected_rule": "owners only", "baseline": "A exports own",
        "changed_variable": "act as B", "observable_result": "403, no data",
        "stopping_point": "one pair", "permission_ref": "P-lab"}})["result"]["case"]
    base = {"method": "GET", "url": f"{url}/export?project=proj_A", "actor_ref": "account-a"}
    var = {"method": "GET", "url": f"{url}/export?project=proj_A", "actor_ref": "account-b"}
    out = rpc(p, 21, "experiment.compare",
              {"run_id": rid, "case_id": res["case_id"],
               "baseline": base, "variant": var})["result"]
    assert out["changed_variable"] == "actor_ref"
    assert out["baseline"]["status"] == 200 and out["variant"]["status"] == 403
    assert out["diff"]["kind"] == "text" and out["diff"]["lines"]  # raw captures
    shown = rpc(p, 22, "case.show", {"run_id": rid, "case_id": res["case_id"]})["result"]["case"]
    assert len(shown["evidence_refs"]) == 2
    diff = rpc(p, 23, "evidence.diff",
               {"run_id": rid, "baseline_id": shown["evidence_refs"][0],
                "variant_id": shown["evidence_refs"][1]})["result"]
    assert diff["kind"] == "text" and diff["lines"]
    bad = rpc(p, 24, "experiment.compare",
              {"run_id": rid, "baseline": base,
               "variant": {**var, "method": "POST", "body": "{}"}})
    assert bad["error"]["code"] == -32602 and "one changed variable" in bad["error"]["message"]


def test_budget_exhaustion_keeps_closeout_resumable(tmp_path):
    mgr = LabManager()
    lab = mgr.launch("tenancy-demo", LABS)
    try:
        port = int(lab["url"].rsplit(":", 1)[1])
        policy = live_policy(port)
        policy["budgets"] = {"requests_total": 2}
        p = start(tmp_path)
        try:
            rpc(p, 1, "initialize", {"protocol_version": "0.1.0"})
            rid = rpc(p, 2, "run.create", {"mode": "live"})["result"]["run_id"]
            rpc(p, 3, "policy.import", {"run_id": rid, "policy": policy})
            get = {"kind": "http.request", "method": "GET",
                   "url": f"{lab['url']}/export?project=proj_A", "actor_ref": "account-a"}
            assert rpc(p, 4, "tool.execute", {"run_id": rid, "action": get})["result"]["status"] == 200
            assert rpc(p, 5, "tool.execute", {"run_id": rid, "action": get})["result"]["status"] == 200
            denied = rpc(p, 6, "tool.execute", {"run_id": rid, "action": get})["result"]
            assert denied["sent"] is False and "exhausted" in " ".join(denied["reasons"])
            closed = rpc(p, 7, "run.close", {"run_id": rid})["result"]
            assert closed == {"run_id": rid, "closed": True}
            assert (tmp_path / "runs" / rid / "summary.md").is_file()
            rpc(p, 8, "shutdown")
        finally:
            p.kill()
    finally:
        mgr.stop_all()


def test_lab_rpc_lifecycle(tmp_path):
    p = start(tmp_path)
    try:
        rpc(p, 1, "initialize", {"protocol_version": "0.1.0"})
        res = rpc(p, 2, "lab.list", {})["result"]
        assert any(l["name"] == "tenancy-demo" for l in res["labs"])
        rpc(p, 3, "shutdown")
    finally:
        p.kill()
