"""Workflow roles (Phase 6, plan.md §9.1). One deterministic orchestrator with
named prompt/tool profiles — NOT hidden autonomous agents.

Each role: typed JSON output schema (strict), tool allowlist, required model
capabilities, and a system prompt that teaches method, never payloads.
The policy engine stays outside every role: role outputs are PROPOSALS.
"""
from __future__ import annotations

from typing import Any

# JSON Schemas for strict structured output per role.
SCHEMAS: dict[str, dict[str, Any]] = {
    "scope_analyst": {
        "type": "object",
        "required": ["facts", "ambiguities", "questions"],
        "properties": {
            "facts": {"type": "array", "items": {"type": "object",
                      "properties": {"claim": {"type": "string"},
                                     "source": {"type": "string"}}, "required": ["claim"]}},
            "ambiguities": {"type": "array", "items": {"type": "string"}},
            "questions": {"type": "array", "items": {"type": "string"}},
        },
    },
    "product_modeler": {
        "type": "object",
        "required": ["actors", "objects", "tenants", "workflows", "trust_boundaries"],
        "properties": {
            "actors": {"type": "array", "items": {"type": "string"}},
            "objects": {"type": "array", "items": {"type": "string"}},
            "tenants": {"type": "array", "items": {"type": "string"}},
            "workflows": {"type": "array", "items": {"type": "string"}},
            "trust_boundaries": {"type": "array", "items": {"type": "string"}},
        },
    },
    "test_designer": {
        "type": "object",
        "required": ["check_id", "hypothesis", "expected_rule", "baseline",
                     "changed_variable", "observable_result", "stopping_point"],
        "properties": {
            "check_id": {"type": "string", "pattern": "^T(0[1-9]|1[0-2])\\.\\d+$"},
            "hypothesis": {"type": "string"}, "expected_rule": {"type": "string"},
            "baseline": {"type": "string"}, "changed_variable": {"type": "string"},
            "observable_result": {"type": "string"}, "stopping_point": {"type": "string"},
        },
    },
    "research_assistant": {
        "type": "object",
        "required": ["next_action", "tool", "bounded_args", "evidence_needed"],
        "properties": {
            "next_action": {"type": "string"},
            "tool": {"type": "string"},
            "bounded_args": {"type": "object"},
            "evidence_needed": {"type": "array", "items": {"type": "string"}},
        },
    },
    "evidence_analyst": {
        "type": "object",
        "required": ["observed", "alternative_explanations", "assessment"],
        "properties": {
            "observed": {"type": "string"},
            "alternative_explanations": {"type": "array", "items": {"type": "string"}},
            "assessment": {"type": "string",
                           "enum": ["supports_rule", "supports_failure", "inconclusive"]},
        },
    },
    "validator": {
        "type": "object",
        "required": ["verdict", "gaps", "demonstrated", "inferred"],
        "properties": {
            "verdict": {"type": "string",
                        "enum": ["validated_finding", "lead", "false_positive",
                                 "tested_no_issue_observed", "inconclusive"]},
            "gaps": {"type": "array", "items": {"type": "string"}},
            "demonstrated": {"type": "string"},
            "inferred": {"type": "string"},
        },
    },
    "report_writer": {
        "type": "object",
        "required": ["title", "asset_scope", "preconditions", "summary", "rule",
                     "reproduction", "expected", "actual", "demonstrated_impact",
                     "evidence", "remediation", "limits"],
        "properties": {
            "title": {"type": "string"}, "asset_scope": {"type": "string"},
            "preconditions": {"type": "string"}, "summary": {"type": "string"},
            "rule": {"type": "string"},
            "reproduction": {"type": "array", "items": {"type": "string"}},
            "expected": {"type": "string"}, "actual": {"type": "string"},
            "demonstrated_impact": {"type": "string"},
            "evidence": {"type": "array", "items": {"type": "string"}},
            "remediation": {"type": "string"}, "limits": {"type": "string"},
            "severity": {"type": "string"},
        },
    },
    "coach": {
        "type": "object",
        "required": ["scores", "feedback", "next_drill"],
        "properties": {
            "scores": {"type": "object",
                       "additionalProperties": {"type": "integer", "minimum": 0, "maximum": 4}},
            "feedback": {"type": "string"},
            "next_drill": {"type": "string"},
        },
    },
}

SYSTEM_PROMPTS: dict[str, str] = {
    "scope_analyst": (
        "You extract authorization facts from the supplied policy text. "
        "Distinguish what the policy STATES from what you INFER. List every "
        "ambiguity that must block live use. Never invent assets, limits, or windows."),
    "product_modeler": (
        "You map the supplied feature description into actors, objects, tenants, "
        "workflows, and trust boundaries. Name the server-enforced rule each "
        "workflow depends on. No payloads, no exploits — model the product."),
    "test_designer": (
        "You design ONE falsifiable experiment: intended rule, clean baseline, "
        "exactly one changed variable, observable result, stopping point. "
        "Harmless minimum proof only. A scanner alert is never a finding."),
    "research_assistant": (
        "You propose the single next bounded action with its exact tool and "
        "arguments. Prefer reading supplied artifacts over any live step. "
        "State what evidence the action must produce."),
    "evidence_analyst": (
        "You compare baseline and variant and list plausible BENIGN explanations "
        "first. Separate observed behavior from inferred impact. One sample never "
        "proves a timing or cache claim."),
    "validator": (
        "You challenge weak conclusions. A lead becomes a finding only with: "
        "in-scope asset, stated rule, controlled identities, baseline plus "
        "one-variable variant, minimum proof, resolved alternatives, and "
        "demonstrated (not inferred) impact. Otherwise name the gaps."),
    "report_writer": (
        "You draft ONLY from validated evidence: exact steps, expected vs actual, "
        "demonstrated impact, evidence refs, limits. Severity is a reasoned "
        "suggestion. Never overclaim triage outcome."),
    "coach": (
        "You score the researcher's decisions 0-4 against the rubric with cited "
        "reasons, then ask one question before giving any hint. Teach method: "
        "rules, controls, evidence quality. Never hand over payloads."),
}

# Role -> allowed tool names (Phase 6 set; live tools arrive gated in Phase 7).
TOOL_ALLOWLISTS: dict[str, tuple[str, ...]] = {
    "scope_analyst": ("artifact.read", "policy.show"),
    "product_modeler": ("artifact.read",),
    "test_designer": ("artifact.read", "case.create"),
    "research_assistant": ("artifact.read", "evidence.show", "http.preview"),
    "evidence_analyst": ("artifact.read", "evidence.show", "evidence.diff"),
    "validator": ("artifact.read", "evidence.show"),
    "report_writer": ("evidence.show", "report.build"),
    "coach": ("artifact.read", "case.show"),
}

# Role -> required model capabilities (router input).
ROLE_REQUIREMENTS: dict[str, frozenset[str]] = {
    "scope_analyst": frozenset({"strict_json"}),
    "product_modeler": frozenset({"strict_json"}),
    "test_designer": frozenset({"strict_json"}),
    "research_assistant": frozenset(),
    "evidence_analyst": frozenset({"strict_json"}),
    "validator": frozenset({"strict_json"}),
    "report_writer": frozenset({"strict_json"}),
    "coach": frozenset({"strict_json"}),
}


def get_role(name: str) -> dict[str, Any]:
    if name not in SCHEMAS:
        raise KeyError(f"unknown role {name!r}")
    return {"name": name, "system": SYSTEM_PROMPTS[name], "schema": SCHEMAS[name],
            "tools": list(TOOL_ALLOWLISTS[name]), "requires": sorted(ROLE_REQUIREMENTS[name])}


def validate_output(role: str, output: Any) -> list[str]:
    """Structural check of a role's typed output. Returns gap strings."""
    schema = SCHEMAS[role]
    gaps: list[str] = []
    if not isinstance(output, dict):
        return ["output is not an object"]
    for key in schema.get("required", []):
        if not output.get(key):
            gaps.append(f"missing {key}")
    props = schema.get("properties", {})
    for key, spec in props.items():
        if key in output and "enum" in spec and output[key] not in spec["enum"]:
            gaps.append(f"{key}={output[key]!r} not in {spec['enum']}")
    return gaps
