"""Golden artifact-only T02 slice (Issue #3): machine-checked proof-gate behavior.

- engagement mode is artifacts with zero request budget (zero egress posture);
- case has one changed variable and required fields;
- variant is a 403 error-only body → result must NOT be validated_finding.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

G = Path(__file__).resolve().parents[3] / "evals/golden/artifact-only-t02"


def load(name):
    return json.loads((G / name).read_text())


def test_zero_egress_posture():
    eng = load("engagement.json")
    assert eng["mode"] == "artifacts"
    assert eng["request_budget"] == 0
    assert "GET" in eng["prohibited_methods"]


def test_case_shape():
    case = load("case.json")
    schema = json.loads((G / "../../../schemas/case.schema.json").resolve().read_text())
    for k in schema["required"]:
        assert k in case, f"missing {k}"
    assert re.match(r"^T(0[1-9]|1[0-2])\.\d+$", case["check_id"])
    for k in ("expected_rule", "baseline", "changed_variable", "observable_result", "stopping_point"):
        assert len(case[k]) > 0


def test_proof_gate_blocks_promotion():
    case = load("case.json")
    variant = load("variant.json")
    assert variant["status"] == 403
    assert "project_id" not in variant["body"]  # error-only: no boundary-crossing data
    assert case["result"] in ("tested_no_issue_observed", "lead", "inconclusive", "blocked")
    assert case["result"] != "validated_finding"
