# Evidence (plan.md §10)

## Originals vs derived

- `artifacts/original/<sha256>` is content-addressed and **immutable**: same
  bytes hash identically and are never overwritten. Tampering is detected by
  re-hash (`evidence.verify`, and implicitly by rebuild).
- `artifacts/derived/<id>` holds transformations (normalization, diffs,
  redacted copies), each recording `derived_from` + transformation.
- Metadata (`evidence/<id>/metadata.json`) carries capture note/tool/actor/
  case refs, redaction state, MIME, size, sensitivity — never a severity claim.

## HTTP evidence

Request/response pairs are stored as one artifact. Deliberate deviation:
**authorization headers are scrubbed at ingest** — auth material lives in
credential refs + actor metadata, never in run records (§5.4). Bodies stay
exact (a leaked token in a body can itself be the finding). Diffs are computed
on derived copies: structural JSON diff, unified text diff, header diff
(cookie/auth values never echoed).

## Redaction

`evidence redact` produces a redacted derived copy; the original is untouched.
Exports scrub text members again at bundle time and record every live shape
found as a manifest **warning** (redaction is never silent). Post-scrub scan
fails closed.

## Reports and closeout

`report build` renders the §10.3 template and **refuses incomplete drafts**,
listing gaps. Evidence refs must resolve to real vault entries — every claim
links to evidence or the build fails. `run close` writes `summary.md` (§10.4),
stating plainly when nothing was validated. Severity is a reasoned suggestion,
never a triage guarantee.
