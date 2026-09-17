# ADR-01: Two-process split (TypeScript TUI + Python engine) over stdio JSON-RPC

Date: 2026-09-16. Status: accepted (Phase 0, Issue #1).

## Context

Need a polished terminal UX + strong security/automation ecosystem. Single-language
options force a compromise: TS is best for TUI, Python for security HTTP/parsing/eval tooling.

## Decision

- `apps/terminal` (TypeScript, OpenTUI React pending spike) owns rendering, input,
  approvals, diffs, command parsing. **No direct target socket, no security-tool subprocess.**
- `services/engine` (Python 3.12+, Pydantic v2, anyio, httpx) owns orchestration,
  policy gate, tools, evidence, state. **Only component allowed to open target sockets
  or spawn security tools.**
- Versioned JSON-RPC 2.0, UTF-8, one message per line; stderr = logs. `packages/protocol-schema`
  is the source of truth; TS + Python types are generated and checked in.

## Consequences

- One enforceable policy boundary (audit the engine egress path, not the UI).
- Launcher must verify engine checksum/signature + protocol compatibility before exec.
- Crash/restart semantics: engine replays from `last_seen_sequence`; UI resumes display.
- Cost: codegen pipeline + cross-language contract tests (required, CI-gated).
