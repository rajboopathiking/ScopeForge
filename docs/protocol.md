# Protocol v0 — JSON-RPC 2.0 over NDJSON stdio

Source of truth: `packages/protocol-schema/protocol.json` (version `0.1.0`).
Regenerate: `pnpm generate:protocol`. Drift check: `pnpm generate:protocol --check` (CI).

## Transport

- stdout: one JSON-RPC 2.0 message per line, UTF-8. stderr: structured JSON logs only.
- Envelope on every message: `protocol_version`, `trace_id`, UTC `ts`,
  plus `run_id` when applicable and `seq` on events.
- Oversize tool output → artifact reference, not inline event.

## Methods (v0)

`initialize` · `capabilities` · `health` · `shutdown` · `run.create` ·
`run.cancel` · `event.subscribe` (test soak hook: `{count, run_id?, from_seq?}`) ·
`tool.cancel`. Unknown → `-32601`. Major mismatch → `1001` fail-closed.

## Events (v0)

`token.delta {seq,index,total,text}` · `reasoning.summary` · `warning` ·
`error` · `run.checkpointed` · `budget.changed`.

## Ordering / reconnect / backpressure / cancellation

- Per-run monotonic `seq`; server seq is globally monotonic in v0 spine.
- Reconnect: client tracks `lastSeenSeq` per run, restarts engine, re-handshakes,
  re-subscribes; dedups by `seq` (server accepts `from_seq`/`last_seen_sequence` anchor).
- Bounded in-memory queue (`QUEUE_BOUND=1000`); emission yields every 250 events so
  `run.cancel`/`tool.cancel` preempts within milliseconds (< 1s criterion).

## Minimal transcript

```jsonl
{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocol_version":"0.1.0","client":"terminal/0.1.0"},"protocol_version":"0.1.0","trace_id":"abc123","ts":"2026-09-16T00:00:00Z"}
{"jsonrpc":"2.0","id":1,"result":{"protocol_version":"0.1.0","engine":"scopeforge-engine/0.1.0","capabilities":{"modes":["plan","artifacts","live"]}},"protocol_version":"0.1.0","trace_id":"abc123","ts":"2026-09-16T00:00:00Z"}
```
