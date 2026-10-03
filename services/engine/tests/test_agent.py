"""Agent runtime tests: roles, turn loop, proof gate, coach, coverage (Phase 6).

Direct library tests with scripted mocks (deterministic, no network).
RPC surface is covered in test_agent_rpc.py.
"""
import json
from pathlib import Path

import pytest

pytest.importorskip("pytest_asyncio")

from scopeforge_engine import roles
from scopeforge_engine.agent import (
    build_context,
    coverage_report,
    dry_run_proposal,
    load_catalog,
    run_role_turn,
    score_case_coach,
)
from scopeforge_engine.coach import AFTER_CASE_QUESTIONS, hint, score_case, weakest_skills
from scopeforge_engine.evidence import EvidenceVault
from scopeforge_engine.models.mock import MockAdapter, turn as build_turn
from scopeforge_engine.proof_gate import evaluate, gate_result_transition
from scopeforge_engine.runs import RunStore

REPO = Path(__file__).resolve().parents[3]
SEED = "AKIAIOSFODNN7SEEDKE1"

DESIGN = {
    "check_id": "T02.1", "hypothesis": "A member from tenant B can export tenant A's project",
    "expected_rule": "Only project members in the owning tenant may export",
    "baseline": "Account A exports its own synthetic fixture",
    "changed_variable": "Authenticate as account B and keep the object reference",
    "observable_result": "Server denies access and returns no project data",
    "stopping_point": "One controlled export attempt after one baseline",
}


def make_store(tmp_path, mode="artifacts"):
    store = RunStore(tmp_path)
    run = store.create_run(mode=mode, engagement={"program_name": "lab"})
    return store, run["run_id"]


def scripted(outputs, model="mock-turn"):
    return MockAdapter(script=[build_turn(
        [{"kind": "text.delta", "text": json.dumps(o)},
         {"kind": "usage", "input_tokens": 10, "output_tokens": 10},
         {"kind": "done", "stop_reason": "stop", "structured_via": "native"}]
    ) for o in outputs])


async def test_designer_turn_validates_and_records(tmp_path):
    store, rid = make_store(tmp_path)
    provider = scripted([DESIGN])
    try:
        result = await run_role_turn(store, rid, "test_designer", provider, model="mock-turn",
                                     user_message="design a T02.1 case")
    finally:
        await provider.close()
    assert result["gaps"] == [] and result["stored"] is True
    assert result["output"]["check_id"] == "T02.1"
    events = [json.loads(line) for line in
              (tmp_path / "runs" / rid / "events.jsonl").read_text().splitlines()]
    assert events and events[-1]["method"] == "agent.turn"


async def test_research_proposal_dry_run_denies_in_artifacts(tmp_path):
    store, rid = make_store(tmp_path)
    proposal = {"next_action": "fetch target", "tool": "http.request",
                "bounded_args": {"method": "GET", "url": "https://authorized.example/x"},
                "evidence_needed": ["response"]}
    provider = scripted([proposal])
    try:
        result = await run_role_turn(store, rid, "research_assistant", provider,
                                     model="mock-turn")
    finally:
        await provider.close()
    assert result["decision"]["allowed"] is False
    assert "zero egress" in " ".join(result["decision"]["reasons"])


async def test_capability_block_visible_without_strict(tmp_path):
    from scopeforge_engine.models.types import ModelCapabilities

    store, rid = make_store(tmp_path)
    weak = MockAdapter(
        script=[],
        capabilities=ModelCapabilities(provider="weak", model="m", adapter="mock",
                                       strict_structured_output=False,
                                       structured_via_tool=False))
    try:
        result = await run_role_turn(store, rid, "test_designer", weak, model="m")
    finally:
        await weak.close()
    assert result["output"] is None and any("capability blocked" in g for g in result["gaps"])
    assert result["stored"] is False


async def test_scanner_false_positive_stays_lead(tmp_path):
    store, rid = make_store(tmp_path)
    vault = EvidenceVault(store, rid)
    alert = vault.import_bytes(b"Server: nginx/1.18.0 (possible CVE-2024-1234)",
                               source_type="note", mime="text/plain")
    case = store.create_case(rid, {
        **DESIGN, "asset": "https://authorized.example", "permission_ref": "P-1",
        "actor_ref": "account-a", "object_ref": "own-fixture",
        "observed_behavior": "scanner flagged a version string; no boundary tested",
    })
    store.update_case(rid, case["case_id"], result="lead",
                      evidence_refs=[alert["evidence_id"]])
    # Validator role judges it: still a lead, with gaps.
    validator_out = {"verdict": "lead", "gaps": ["no boundary demonstration"],
                     "demonstrated": "version string only", "inferred": "possible CVE"}
    provider = scripted([validator_out])
    try:
        result = await run_role_turn(store, rid, "validator", provider, model="mock-turn",
                                     case_id=case["case_id"])
    finally:
        await provider.close()
    assert result["output"]["verdict"] == "lead"
    # Record the verdict; promotion is now REFUSED (binding).
    store.update_case(rid, case["case_id"], validator_verdict="lead",
                      validator_gaps=result["output"]["gaps"])
    gate = gate_result_transition(
        {**store.get_case(rid, case["case_id"]), "result": "validated_finding"},
        [vault.get(alert["evidence_id"])], "validated_finding")
    assert gate["allowed"] is False
    assert any("validator verdict is lead" in g for g in gate["gaps"])


async def test_full_case_passes_structural_gate(tmp_path):
    store, rid = make_store(tmp_path)
    vault = EvidenceVault(store, rid)
    base = vault.import_bytes(b'{"project": "ok"}', source_type="note", mime="application/json")
    var = vault.import_bytes(b'{"error": "forbidden"}', source_type="note",
                             mime="application/json")
    case = store.create_case(rid, {
        **DESIGN, "asset": "https://authorized.example", "permission_ref": "P-1",
        "actor_ref": "account-b", "object_ref": "fixture-owned-by-a",
        "observed_behavior": "403 error-only; no project fields",
    })
    store.update_case(rid, case["case_id"], result="lead",
                      evidence_refs=[base["evidence_id"], var["evidence_id"]])
    gate = gate_result_transition(
        {**store.get_case(rid, case["case_id"]), "result": "validated_finding"},
        [vault.get(base["evidence_id"]), vault.get(var["evidence_id"])],
        "validated_finding")
    assert gate == {"allowed": True, "verdict": "validated_finding", "gaps": []}
    assert evaluate(store.get_case(rid, case["case_id"]),
                    [vault.get(base["evidence_id"])])["passed"] is False  # missing ref


async def test_golden_t02_artifact_flow_no_finding(tmp_path):
    """End-to-end artifacts-mode T02.1: baseline/variant evidence, analyst,
    validator says no-issue, closeout states it plainly."""
    store, rid = make_store(tmp_path)
    vault = EvidenceVault(store, rid)
    golden = REPO / "evals" / "golden" / "artifact-only-t02"
    baseline = vault.import_file(golden / "baseline.json", source_type="artifact",
                                 actor_ref="account-a", case_refs=[])
    variant = vault.import_file(golden / "variant.json", source_type="artifact",
                                actor_ref="account-b", case_refs=[])
    case = store.create_case(rid, {
        **DESIGN, "asset": "file://labs/tenancy-demo", "permission_ref": "P-2026-09-15",
        "actor_ref": "account-b", "role": "member", "tenant_ref": "tenant-b",
        "object_ref": "fixture-owned-by-account-a",
    })
    store.update_case(rid, case["case_id"],
                      result="tested_no_issue_observed",
                      observed_behavior="variant 403 error-only; no project fields present",
                      evidence_refs=[baseline["evidence_id"], variant["evidence_id"]])
    analyst_out = {"observed": "403 error-only variant vs 200 baseline",
                   "alternative_explanations": ["fixture misconfiguration"],
                   "assessment": "supports_rule"}
    validator_out = {"verdict": "tested_no_issue_observed", "gaps": [],
                     "demonstrated": "denial with no data", "inferred": ""}
    provider = scripted([analyst_out, validator_out])
    try:
        a = await run_role_turn(store, rid, "evidence_analyst", provider,
                                model="mock-turn", case_id=case["case_id"])
        v = await run_role_turn(store, rid, "validator", provider,
                                model="mock-turn", case_id=case["case_id"])
    finally:
        await provider.close()
    assert a["output"]["assessment"] == "supports_rule"
    assert v["output"]["verdict"] == "tested_no_issue_observed"
    # A 403 error-only body must never promote: verdict mismatch blocks.
    store.update_case(rid, case["case_id"],
                      validator_verdict="tested_no_issue_observed", validator_gaps=[])
    gate = gate_result_transition(
        {**store.get_case(rid, case["case_id"]), "result": "validated_finding"},
        [vault.get(baseline["evidence_id"])], "validated_finding")
    assert gate["allowed"] is False


async def test_context_is_secret_free(tmp_path):
    store, rid = make_store(tmp_path)
    vault = EvidenceVault(store, rid)
    meta = vault.import_bytes(f"session {SEED} leaked".encode(), source_type="note",
                              mime="text/plain")
    case = store.create_case(rid, {**DESIGN, "asset": "a", "permission_ref": "P"})
    store.update_case(rid, case["case_id"], result="lead",
                      observed_behavior="note", evidence_refs=[meta["evidence_id"]])
    ctx = build_context(store, rid, case_id=case["case_id"], user_message=f"hi {SEED}")
    assert SEED not in json.dumps(ctx)


def test_coach_scores_cite_actual_fields(tmp_path):
    store, rid = make_store(tmp_path)
    case = store.create_case(rid, {**DESIGN, "asset": "a", "permission_ref": "P-1"})
    result = score_case_coach(store, rid, case["case_id"])
    scores = result["scores"]
    assert set(scores) == {"scope_discipline", "product_modeling", "hypothesis_quality",
                           "experimental_control", "impact_restraint", "evidence_quality",
                           "alternative_explanations", "reporting", "retesting", "tool_literacy"}
    assert any("permission_ref" in r for r in scores["scope_discipline"]["reasons"])
    assert len(result["questions"]) == len(AFTER_CASE_QUESTIONS) == 7
    assert "T02.1" in hint(1, "hypothesis_quality", case)
    assert "Concept" in hint(2, "hypothesis_quality", case)
    assert "Concrete" in hint(3, "hypothesis_quality", case)
    assert score_case(case, 0)["evidence_quality"]["score"] <= 1
    assert weakest_skills([{"case_id": "C-1", "scores": scores}])[0] in scores


def test_coverage_catalog_not_executed_by_default(tmp_path):
    catalog = load_catalog(REPO / "curriculum" / "coverage-catalog.json")
    assert len(catalog["categories"]) == 12
    assert sum(len(c["checks"]) for c in catalog["categories"]) >= 24
    store, rid = make_store(tmp_path)
    report = coverage_report(store, rid, catalog)
    statuses = {c["status"] for cat in report["categories"] for c in cat["checks"]}
    assert statuses == {"not_executed"}  # catalog prompts alone are not coverage
    store.create_case(rid, {**DESIGN, "asset": "a", "permission_ref": "P"})
    report = coverage_report(store, rid, catalog)
    t02 = [c for cat in report["categories"] if cat["id"] == "T02" for c in cat["checks"]]
    assert [c for c in t02 if c["check_id"] == "T02.1"][0]["status"] == "planned"
    assert [c for c in t02 if c["check_id"] == "T02.2"][0]["status"] == "not_executed"


def test_roles_registry():
    assert roles.get_role("validator")["requires"] == ["strict_json"]
    with pytest.raises(KeyError):
        roles.get_role("rogue")
    assert roles.validate_output("validator", {"verdict": "nope"}) != []
    assert roles.validate_output("test_designer", dict(DESIGN)) == []
    assert dry_run_proposal.__name__ == "dry_run_proposal"
