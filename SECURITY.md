# Security Policy

## Supported versions

MVP / pre-1.0: best-effort patches on `main`. Security fixes are prioritized
over features. After 1.0, the two most recent minor lines are supported.

## Do not test live targets with this tool unless authorized

ScopeForge is for **authorized** research and deliberate practice on
researcher-controlled fixtures / local labs / explicitly authorized programs.
Empty scope means unknown, never unrestricted.

## Reporting a vulnerability in ScopeForge itself

- Email / private channel: TBD during Phase 0 (see ADR-08 signing/release identity).
  Until published, use a private GitHub Security Advisory for this repo.
- Include: affected version/commit, repro steps, impact, whether secrets/egress/policy
  bypass is involved, logs (redacted).
- Expect acknowledgement within 72h, triage within 7 days, fix timeline proportional
  to severity. Critical policy-bypass / egress / secret-leak issues are P0.

## Security-sensitive code ownership (§16)

`policy`, `egress`, `sandbox`, `secrets`, `updater`, `archive parsing`,
`export redaction` require careful review. Two-person review required when
maintainer base permits it.

## What we promise

- `plan` / `artifacts` modes have zero target egress (integration-tested, CI-gated).
- No telemetry by default (§17). No prompts/targets/policy/evidence/credentials leave
  the machine unless the user explicitly enables a provider or shares a redacted bundle.
- SBOM + provenance + signed releases on tagged builds (Phase 9).

## Out of scope for the harness threat model

Mass scanning, credential attacks, persistence, stealth/evasion, exploitation at
scale, destructive proof, auto-claiming infrastructure, auto-submission —
there is no built-in execution path (R6 prohibited, §7.5).
