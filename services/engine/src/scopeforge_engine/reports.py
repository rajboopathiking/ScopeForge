"""Report + closeout renderers (Phase 4, plan.md §10.3–10.4). Stdlib only.

Reports refuse incomplete drafts (missing-section errors), so an unvalidated
narrative can never render as a finished finding by accident. The full proof
gate (validator role) lands in Phase 6 and calls these same renderers.
"""
from __future__ import annotations

REPORT_REQUIRED = (
    "title", "asset_scope", "preconditions", "summary", "rule",
    "reproduction", "expected", "actual", "demonstrated_impact",
    "evidence", "remediation", "limits",
)


def render_finding_report(finding: dict) -> str:
    gaps = [key for key in REPORT_REQUIRED if not finding.get(key)]
    if gaps:
        raise ValueError(f"incomplete draft, missing: {', '.join(gaps)}")
    ev = finding["evidence"]
    ev_lines = "\n".join(f"- {ref}" for ref in (ev if isinstance(ev, list) else [ev]))
    steps = finding["reproduction"]
    step_lines = "\n".join(
        f"{i + 1}. {s}" for i, s in enumerate(steps if isinstance(steps, list) else [steps]))
    severity = finding.get("severity", "")
    sev_line = f"\n**Suggested severity:** {severity}\n" if severity else ""
    return f"""# {finding['title']}

{sev_line}
## Asset and scope

{finding['asset_scope']}

## Preconditions and controlled fixtures

{finding['preconditions']}

## Summary

{finding['summary']}

## Security rule

{finding['rule']}

## Reproduction steps

{step_lines}

## Expected result

{finding['expected']}

## Actual result

{finding['actual']}

## Demonstrated impact

{finding['demonstrated_impact']}

## Evidence

{ev_lines}

## Suggested remediation

{finding['remediation']}

## Validation limits and untested consequences

{finding['limits']}
"""


def render_closeout(data: dict) -> str:
    """summary.md per §10.4. Says plainly when nothing was validated."""
    cases = data.get("cases", [])
    findings = data.get("findings", [])
    case_lines = "\n".join(
        f"- {c.get('case_id', '?')} {c.get('check_id', '?')} "
        f"{c.get('result', '?')}: {c.get('hypothesis', '')[:100]}" for c in cases)
    finding_lines = "\n".join(f"- {f}" for f in findings) or "- none validated"
    no_finding_note = ("No findings were validated in this run."
                       if not findings else f"{len(findings)} validated finding(s) above.")
    return f"""# Run closeout: {data.get('run_id', '?')}

- Mode: {data.get('mode', '?')} (target egress: {data.get('egress', 'none')})
- Scope: {data.get('scope', '?')} (as of {data.get('scope_date', '?')})
- Requested vs performed: {data.get('requested', '?')} vs {data.get('performed', '?')}

## Cases ({len(cases)})

{case_lines or '(none)'}

## Validated findings

{finding_lines}

{no_finding_note}

## Unresolved leads

{data.get('leads', '(none)')}

## Blocked and untested work

{data.get('blocked', '(none)')}

## Budgets used

{data.get('budgets', '(none recorded)')}

## Retest status

{data.get('retest', 'not requested')}

## Evidence and reports

{data.get('locations', '(see run directory)')}

## Next-run queue

{data.get('next_queue', '(empty)')}
"""
