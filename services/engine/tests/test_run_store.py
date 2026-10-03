"""Run store, vault, parsers, reports, DB, export — direct unit tests (stdlib)."""
from __future__ import annotations

import io
import json
import tarfile
import zipfile

import pytest

from scopeforge_engine import coverage_skeleton, parsers, reports
from scopeforge_engine.db import rebuild
from scopeforge_engine.evidence import EvidenceVault, link_case
from scopeforge_engine.exporter import create_export
from scopeforge_engine.runs import RunStore, ValidationError
from scopeforge_engine.store import CorruptStore, FileLock, append_jsonl, read_jsonl

VALID_CASE = {
    "check_id": "T02.1", "asset": "https://authorized.example",
    "hypothesis": "member B exports A's project", "expected_rule": "owners only",
    "baseline": "A exports own fixture", "changed_variable": "act as B",
    "observable_result": "403, no data", "stopping_point": "one attempt",
    "permission_ref": "P-1",
}
SEED = "AKIAIOSFODNN7SEEDKE1"


def make_run(store=None, tmp_path=None, **kw):
    store = store or RunStore(tmp_path)
    return store, store.create_run(mode="plan", engagement={"program_name": "lab"}, **kw)


def test_run_create_defaults_and_budgets(tmp_path):
    store, run = make_run(tmp_path=tmp_path)
    assert run["status"] == "draft" and run["run_id"] == "run_0001"
    eng = json.loads((tmp_path / "runs" / "run_0001" / "engagement.json").read_text())
    assert eng["request_budget"] == 0  # plan mode: zero egress budget
    assert [r["run_id"] for r in store.list_runs()] == ["run_0001"]
    with pytest.raises(ValidationError):
        store.get_run("run_9999")


def test_case_crud_and_execution_gate(tmp_path):
    store, run = make_run(tmp_path=tmp_path)
    rid = run["run_id"]
    with pytest.raises(ValidationError):
        store.create_case(rid, {"check_id": "bogus"})
    case = store.create_case(rid, dict(VALID_CASE))
    assert case["case_id"] == "C-0001" and case["result"] == "queued"
    assert store.get_case(rid, "C-0001")["hypothesis"].startswith("member B")
    with pytest.raises(ValidationError, match="evidence_refs"):
        store.update_case(rid, "C-0001", result="lead")  # executed needs evidence
    ev = EvidenceVault(store, rid).import_bytes(b"{}", source_type="note")
    updated = store.update_case(rid, "C-0001", result="lead", evidence_refs=[ev["evidence_id"]])
    assert updated["observed_at"] and updated["result"] == "lead"
    assert store.list_cases(rid, "lead")[0]["case_id"] == "C-0001"
    with pytest.raises(ValidationError):
        store.update_case(rid, "C-9999", result="lead")


def test_evidence_immutable_and_verified(tmp_path):
    store, run = make_run(tmp_path=tmp_path)
    vault = EvidenceVault(store, run["run_id"])
    a = vault.import_bytes(b"hello", source_type="note", description="a")
    b = vault.import_bytes(b"hello", source_type="note", description="b")
    assert a["sha256"] == b["sha256"] and a["evidence_id"] != b["evidence_id"]
    assert vault.verify(a["evidence_id"]) is True
    blob = tmp_path / "runs" / run["run_id"] / "artifacts" / "original" / a["sha256"][:2] / a["sha256"]
    blob.write_bytes(b"tampered")
    assert vault.verify(a["evidence_id"]) is False
    derived = vault.derive(a["evidence_id"], b"HELLO", "uppercase")
    assert derived["original"] is False and derived["derived_from"] == a["evidence_id"]


def test_evidence_redact_and_link(tmp_path):
    store, run = make_run(tmp_path=tmp_path)
    rid = run["run_id"]
    vault = EvidenceVault(store, rid)
    meta = vault.import_bytes(f"token {SEED} here".encode(), source_type="note",
                              mime="text/plain")
    red = vault.redact(meta["evidence_id"])
    assert red["redaction_state"] == "redacted"
    assert SEED.encode() not in vault.read_bytes(red["evidence_id"], derived_ok=True)
    assert SEED.encode() in vault.read_bytes(meta["evidence_id"])  # original intact
    case = store.create_case(rid, dict(VALID_CASE))
    linked = link_case(store, rid, case["case_id"], meta["evidence_id"])
    assert linked["evidence_refs"] == [meta["evidence_id"]]


def test_file_lock_and_jsonl_strictness(tmp_path):
    target = tmp_path / "x.jsonl"
    append_jsonl(target, {"a": 1})
    assert read_jsonl(target) == [{"a": 1}]
    with open(target, "a") as fh:
        fh.write("{broken\n")
    with pytest.raises(CorruptStore):
        read_jsonl(target)
    with FileLock(tmp_path / "l.lock"):
        pass


def test_parsers_and_diffs():
    har = {"log": {"entries": [{
        "request": {"method": "GET", "url": "https://a.example/x",
                    "headers": [{"name": "H", "value": "v"}]},
        "response": {"status": 200, "headers": [], "content": {"text": "ok"}},
        "timings": {}}]}}
    assert parsers.parse_har(har)[0]["status"] == 200
    with pytest.raises(parsers.ParseError):
        parsers.parse_har({"nope": 1})
    req = parsers.parse_raw_http_request("GET /x HTTP/1.1\nHost: a\n\nbody")
    assert req["method"] == "GET" and req["body"] == "body"
    resp = parsers.parse_raw_http_response("HTTP/1.1 403 Forbidden\nX-A: b\n\nno")
    assert resp["status"] == 403
    api = parsers.parse_openapi({"paths": {"/exp": {"get": {"operationId": "e",
        "parameters": [{"name": "id"}], "security": [{}]}}}})
    assert api[0]["auth"] is True and api[0]["params"] == ["id"]
    gql = parsers.parse_graphql_sdl("type Query { user(id: ID): User } type Mutation { exp: E }")
    assert "Query" in gql["types"]
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("../evil", b"x")
    with pytest.raises(parsers.ParseError, match="unsafe member"):
        parsers.parse_archive(buf.getvalue(), "a.zip")
    diffs = parsers.diff_json({"a": 1, "b": {"c": 2}}, {"a": 1, "b": {"c": 3}, "d": 4})
    kinds = {(d["path"], d["kind"]) for d in diffs}
    assert ("$.b.c", "changed") in kinds and ("$.d", "added") in kinds
    assert any(l.startswith("+") for l in parsers.diff_text("a\n", "a\nb\n"))
    hd = parsers.diff_headers({"X-A": "1", "Cookie": "s=1"}, {"X-A": "2"})
    assert {h["header"] for h in hd} == {"x-a", "cookie"}
    assert "before" not in [h for h in hd if h["header"] == "cookie"][0]  # secrets not echoed


def test_reports_refuse_and_render():
    with pytest.raises(ValueError, match="missing"):
        reports.render_finding_report({"title": "x"})
    finding = {k: f"{k}-text" for k in reports.REPORT_REQUIRED}
    finding["evidence"] = ["E-001"]
    finding["reproduction"] = ["step one", "step two"]
    text = reports.render_finding_report(finding)
    assert "## Demonstrated impact" in text and "1. step one" in text
    closeout = reports.render_closeout({"run_id": "run_0001", "mode": "artifacts",
                                        "cases": [], "findings": []})
    assert "No findings were validated" in closeout


def test_db_rebuild_equivalence(tmp_path):
    store, run = make_run(tmp_path=tmp_path)
    rid = run["run_id"]
    store.create_case(rid, dict(VALID_CASE))
    EvidenceVault(store, rid).import_bytes(b"{}", source_type="note")
    counts = rebuild(tmp_path)
    assert counts == {"runs": 1, "cases": 1, "evidence": 1}
    counts2 = rebuild(tmp_path)  # rebuildable: same result twice
    assert counts2 == counts


def test_export_redacted_with_warnings_never_silent(tmp_path):
    store, run = make_run(tmp_path=tmp_path)
    rid = run["run_id"]
    vault = EvidenceVault(store, rid)
    clean = vault.import_bytes(b'{"ok": true}', source_type="note", mime="application/json")
    dirty = vault.import_bytes(f"leaked {SEED}".encode(), source_type="note",
                               mime="text/plain")
    vault.redact(dirty["evidence_id"])
    case = store.create_case(rid, dict(VALID_CASE))
    store.update_case(rid, case["case_id"], result="lead",
                      observed_behavior=f"saw {SEED} in output",
                      evidence_refs=[clean["evidence_id"]])
    result = create_export(store, rid)
    # Bundle is clean, but the live secret in the run is REPORTED, not laundered.
    assert any("cases.jsonl" in w and "aws-access-key" in w for w in result["warnings"])
    assert result["sha256"] and result["files"] > 0
    with tarfile.open(result["path"], "r:gz") as tf:
        names = tf.getnames()
        assert "manifest.json" in names
        assert not any("/original/" in n for n in names)  # originals excluded
        blob = b"".join(tf.extractfile(n).read() for n in names
                        if n.endswith((".json", ".jsonl", ".md")))
        manifest = json.loads(tf.extractfile("manifest.json").read())
    assert SEED.encode() not in blob
    assert manifest["warnings"] == result["warnings"]
    assert coverage_skeleton.skeleton()["categories"].__len__() == 12
