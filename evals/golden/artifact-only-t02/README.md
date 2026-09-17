# Golden run: artifact-only T02.1 (no live traffic) — Issue #3

Proves the first vertical slice runs end-to-end without target egress:
import → case → evidence diff → proof gate (stays non-finding) → report → closeout → resume.

## Inputs (`evals/golden/artifact-only-t02/`)

- `engagement.json` — mode `artifacts`, zero request budget, fixture-backed authorization basis.
- `baseline.json` — supplied artifact: Account A exports its own fixture (200 + project fields).
- `variant.json` — supplied artifact: Account B requests Account A's object (403 error-only).
- `case.json` — concrete T02.1 case (one changed variable: actor), `permission_ref` P-2026-09-15.

## Expected machine-checked outcomes

1. Mode is `artifacts`; engine opens **zero sockets** (integration test asserts).
2. `case.json` validates against `schemas/case.schema.json`.
3. Evidence entries `E-001`/`E-002` hash-match bytes; derived redacted copies recorded.
4. Proof gate: result MUST be `tested_no_issue_observed` (or `lead` at most) —
   a 403 error-only body never promotes to `validated_finding` (validator test).
5. Report renders from `report-template.md` with Expected/Actual/Demonstrated-impact/Limits.
6. `summary.md` closeout states plainly: no validated findings; coverage T02.1 done.
7. Resume: re-ingesting the directory reproduces identical case result + hashes.

See `tests/golden/test_artifact_only_t02.py` (engine) and the TS contract fixture
`tests/contract/fixtures/golden-t02.json`.
