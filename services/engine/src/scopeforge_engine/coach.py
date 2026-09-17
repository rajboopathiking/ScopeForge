"""Coach: rubric scoring, hints, mastery history (Phase 6, plan.md §11).

Scores are heuristic-over-evidence (0–4 per skill with cited reasons from the
actual case record) — never a vague LLM "expert score". The coach ROLE may
propose wording; THESE functions compute the numbers.
"""
from __future__ import annotations

SKILLS = (
    "scope_discipline", "product_modeling", "hypothesis_quality",
    "experimental_control", "impact_restraint", "evidence_quality",
    "alternative_explanations", "reporting", "retesting", "tool_literacy",
)

SKILL_GUIDANCE = {
    "scope_discipline": "Name inclusions, exclusions, methods, and stop conditions explicitly.",
    "product_modeling": "Map actors, objects, tenants, and the server-enforced rule first.",
    "hypothesis_quality": "State the intended rule AND the exact failure you expect.",
    "experimental_control": "One clean baseline, one changed variable, nothing else.",
    "impact_restraint": "Prove the boundary with the smallest harmless observation.",
    "evidence_quality": "Capture replayable evidence with identity and ownership visible.",
    "alternative_explanations": "List benign causes and rule them out before claiming.",
    "reporting": "Exact steps, expected vs actual, demonstrated impact, limits.",
    "retesting": "Reproduce from clean state; test justified neighbors.",
    "tool_literacy": "Validate tool output manually; know what the tool cannot see.",
}

AFTER_CASE_QUESTIONS = (
    "What rule did you expect?",
    "What changed between baseline and variant?",
    "What observation would falsify your claim?",
    "Which evidence proves the active identity and object ownership?",
    "What is demonstrated versus inferred?",
    "Why is this the minimum sufficient proof?",
    "What should the next bounded case be?",
)


def _len(text) -> int:
    return len((text or "").strip())


def score_case(case: dict, evidence_count: int = 0) -> dict[str, dict]:
    """Score a case record. Each skill -> {score, reasons[]} citing actual fields."""
    c = case
    out: dict[str, dict] = {}

    def put(skill: str, score: int, reasons: list[str]) -> None:
        out[skill] = {"score": max(0, min(4, score)), "reasons": reasons}

    put("scope_discipline", 2 + bool(c.get("permission_ref")) + bool(c.get("stopping_point")),
        ["permission_ref present" if c.get("permission_ref") else "missing permission_ref",
         "stopping_point present" if c.get("stopping_point") else "missing stopping_point"])
    actors = bool(c.get("actor_ref")) + bool(c.get("object_ref")) + bool(c.get("tenant_ref"))
    put("product_modeling", min(4, 1 + actors),
        [f"{actors}/3 identity references (actor/object/tenant)"])
    hypo = _len(c.get("hypothesis")) + _len(c.get("expected_rule"))
    put("hypothesis_quality", 0 if hypo == 0 else (2 if hypo < 60 else 3) + bool(_len(c.get("expected_rule")) > 10),
        ["rule+hypothesis length"] )
    put("experimental_control",
        2 + bool(_len(c.get("baseline")) > 10) + bool(_len(c.get("changed_variable")) > 5),
        ["baseline and one changed variable stated" if c.get("baseline") and c.get("changed_variable")
         else "baseline/variant incomplete"])
    put("impact_restraint", 3 if not c.get("inferred_impact") else 2,
        ["no inflated consequence claimed" if not c.get("inferred_impact")
         else "inferred impact present: check it stays separate from proof"])
    put("evidence_quality", min(4, evidence_count + (1 if c.get("observed_behavior") else 0)),
        [f"{evidence_count} linked evidence record(s)"])
    put("alternative_explanations", 1,
        ["analyst must record ruled-out benign causes (validator prompt covers this)"])
    put("reporting", 2 if c.get("observed_behavior") else 1,
        ["observed behavior recorded" if c.get("observed_behavior") else "no observation yet"])
    put("retesting", 1, ["baseline for retest practice"])
    put("tool_literacy", 2, ["artifact-only: outputs manually inspected by construction"])
    return out


def hint(hint_level: int, skill: str, case: dict) -> str:
    """Three progressive levels: question, concept, concrete next observation."""
    if hint_level <= 1:
        return f"Question before hints: what rule do you expect to hold for {case.get('check_id', 'this check')}?"
    if hint_level == 2:
        return f"Concept: {SKILL_GUIDANCE.get(skill, 're-read the case fields')}."
    check = case.get("check_id", "")
    return (f"Concrete next observation: capture the baseline response for {check} "
            f"as {case.get('actor_ref', 'the owning identity')}, then change exactly one "
            f"variable ({case.get('changed_variable', 'the actor')}) and diff.")


def record_mastery(entries: list[dict], case_id: str, scores: dict) -> list[dict]:
    entries.append({"case_id": case_id, "scores": scores})
    return entries


def weakest_skills(history: list[dict], k: int = 2) -> list[str]:
    totals: dict[str, list[int]] = {s: [] for s in SKILLS}
    for entry in history:
        for skill, rec in (entry.get("scores") or {}).items():
            if skill in totals:
                totals[skill].append(rec.get("score", 0))
    ranked = sorted(SKILLS, key=lambda s: (sum(totals[s]) / len(totals[s])) if totals[s] else 0)
    return ranked[:k]
