# ADR-02: Protocol v0 (JSON-RPC 2.0 over NDJSON stdio)

Date: 2026-09-16. Status: accepted. Implements plan.md §5.3 + Issue #2.

## Decisions

- Transport: newline-delimited JSON, UTF-8, one JSON-RPC 2.0 message per line on
  stdout; structured JSON logs on stderr. No remote transport in MVP.
- Envelope (every message): `protocol_version`, `trace_id`, UTC `ts`,
  `run_id` when applicable, monotonic `seq` on events, redaction classification.
- Methods v0: `initialize`, `capabilities`, `health`, `shutdown`, `run.create`,
  `run.cancel`, `event.subscribe` (test hook for 10k-event soak), `tool.cancel`.
  Full method set (chat/case/approval/provider/tool/artifact/report/export) lands
  incrementally; unknown methods → JSON-RPC `-32601`.
- Events v0: `token.delta`, `warning`, `error`, `run.checkpointed`, `budget.changed`.
- Compatibility: major mismatch → fail closed with actionable message;
  minor → capability negotiation; unknown fields preserved where practical,
  unknown events ignored with warning.
- Ordering: per-run monotonic `seq`; reconnect replays from `last_seen_sequence`;
  bounded in-memory queues apply backpressure; cancellation propagates to
  model stream/HTTP/subprocess/browser.
- Oversize tool output → artifact reference, not inline event.

## Verification

- `tests/contract/` replays TS/Python fixtures; `tests/e2e/spine.test.ts` asserts
  10k ordered events without loss, cancel < 1s, major-version rejection.
