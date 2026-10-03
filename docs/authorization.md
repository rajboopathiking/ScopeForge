# Authorization model (plan.md §7)

Modes: `plan` (never contacts targets) · `artifacts` (never; supplied files only) ·
`live` (only after policy gate, persisted state transition with review screen).

## Decision pipeline (deterministic, in order)

1. Run mode allows live contact? 2. Policy source/date fresh, re-confirm material change?
3. Asset match (canonical scheme/host/port/path vs include, no exclusion)?
4. DNS: all resolved addresses allowed? Re-resolve safely; rebind defense.
5. Redirect: each hop independently in scope? Never forward creds cross-origin unapproved.
6. Method/tool/payload class permitted? 7. Identity/fixture researcher-controlled?
8. Impact class acceptable (R0–R6)? 9. Budgets (request/rate/concurrency/time/byte/token/cost)?
10. Approval (one-shot/session)? 11. Mint short-lived single-purpose execution token.
12. Postcondition: record counts/destination/timing/output class/stop signal.

No approval skips 1–10. Executor rejects expired/replayed/broadened/mismatched capabilities.

## Scope matcher

Canonicalize IDNA/scheme/ports/dot-segments/IPv4/IPv6/trailing dots. Structured rules
(exact hosts, declared subdomain patterns, CIDRs, ports, path prefixes, envs, API versions).
Deny ambiguous wildcards. Block link-local/metadata/loopback/RFC1918/Unix sockets unless the
exact lab asset is explicitly authorized. Pin checked address or proxy. Re-evaluate every
redirect/navigation/WebSocket/callback/discovered URL. Strip auth on origin change.

## Tool risk classes

R0 local read · R1 local write (audit) · R2 bounded passive network (confirm) ·
R3 live target read (gate + preview) · R4 live state change (one-shot + fixture proof) ·
R5 shared/high-impact (disabled unless isolationexplicit) · R6 prohibited (no execution path).

Empty scope = unknown, never unrestricted. Missing numeric limit = unknown, never unlimited.
User-chosen conservative operating budgets are labeled researcher limits, not program rules.
