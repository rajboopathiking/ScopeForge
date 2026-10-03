# Methodology (plan.md §6)

Work is structured as scope → recon/model → plan → test → validate → report →
retest/closeout. The unit of work is the **concrete case**: one falsifiable
experiment with a stated rule, clean baseline, exactly one changed variable,
observable result, and stopping point (`schemas/case.schema.json`).

## Coverage (T01–T12)

`curriculum/coverage-catalog.json` lists concrete checks per category. Status is
**derived from executed cases**, never from reading the catalog:

- `not_executed` — no case references the check;
- `planned` — queued cases only;
- `tested` — an executed result with linked evidence;
- `finding` — a validated finding.

See `coverage.report`. Catalog prompts alone never count as coverage.

## Validation and the proof gate

A lead becomes a finding only through `proof_gate`: in-scope asset, permission
reference, stated rule, controlled identities, baseline + one-variable variant,
linked replayable evidence, observed (not merely inferred) impact, honored
stopping point — plus a recorded validator verdict when the validator reviewed
the case. Scanner output, version strings, lone status codes, and reflections
never promote. `report.build` refuses ungated cases, naming the gaps.

## Closeout

`run close` renders `summary.md`: requested vs performed, coverage incl.
negative observations, validated findings, unresolved leads, blocked work,
budgets, evidence locations, next queue. No validated findings is stated
plainly — silence is not a result.
