"""Finding proof gate (Phase 6, plan.md §6.4). Deterministic, not vibes.

A case result may become `validated_finding` only when every applicable check
passes. Anything else keeps it a lead (or worse) with named gaps. The validator
ROLE proposes; THIS module disposes — prompts cannot promote findings.
"""
from __future__ import annotations

from typing import Any

EXECUTED = ("tested_no_issue_observed", "lead", "validated_finding",
            "false_positive", "blocked", "inconclusive")


def evaluate(case: dict, evidence: list[dict]) -> dict[str, Any]:
    """Returns {passed, gaps, verdict}. `verdict` is the strongest ALLOWED result."""
    gaps: list[str] = []
    by_id = {e.get("evidence_id"): e for e in evidence}
    refs = case.get("evidence_refs", []) or []
    linked = [by_id[r] for r in refs if r in by_id]

    if not case.get("asset"):
        gaps.append("no exact in-scope asset")
    if not case.get("permission_ref"):
        gaps.append("no permission reference")
    if not case.get("expected_rule"):
        gaps.append("no intended security rule stated")
    if not case.get("actor_ref") or not case.get("object_ref"):
        gaps.append("controlled identity or fixture ownership unknown")
    if not case.get("baseline") or not case.get("changed_variable"):
        gaps.append("missing baseline or one-variable variant design")
    if not refs:
        gaps.append("no evidence references")
    if refs and not linked:
        gaps.append(f"{len(refs)} evidence ref(s) do not resolve")
    if refs and linked and len(linked) < len(refs):
        gaps.append(f"{len(refs) - len(linked)} evidence ref(s) do not resolve")
    if not case.get("observed_behavior"):
        gaps.append("no observed behavior recorded")
    if case.get("observed_behavior") and not linked:
        gaps.append("observation has no linked evidence to replay")
    inferred = (case.get("inferred_impact") or "").strip()
    demonstrated = (case.get("observed_behavior") or "").strip()
    if inferred and not demonstrated:
        gaps.append("inferred consequence without demonstrated observation")
    if case.get("result") == "validated_finding" and not case.get("stopping_point"):
        gaps.append("no stopping point honored")
    verdict_recorded = (case.get("validator_verdict") or "").strip()
    if verdict_recorded and verdict_recorded != "validated_finding":
        gaps.append(f"validator verdict is {verdict_recorded}, not validated_finding")

    if gaps:
        verdict = "lead" if case.get("result") in ("validated_finding", "lead", None) else case.get("result", "lead")
        return {"passed": False, "gaps": gaps, "verdict": verdict}
    return {"passed": True, "gaps": [], "verdict": "validated_finding"}


def gate_result_transition(case: dict, evidence: list[dict], new_result: str) -> dict[str, Any]:
    """Authoritative transition check for case.update and report.build."""
    if new_result != "validated_finding":
        return {"allowed": True, "verdict": new_result, "gaps": []}
    trial = {**case, "result": "validated_finding"}
    verdict = evaluate(trial, evidence)
    if verdict["passed"]:
        return {"allowed": True, "verdict": "validated_finding", "gaps": []}
    return {"allowed": False, "verdict": verdict["verdict"], "gaps": verdict["gaps"]}
