# ADR-03: Run store — portable files authoritative, SQLite rebuildable index

Date: 2026-09-16. Status: accepted. Implements plan.md §5.4.

## Decisions

- Layout per `.scopeforge/runs/<run-id>/`: `engagement.json`, `run-state.json`,
  `coverage.json`, `cases.jsonl`, `events.jsonl`, `model-usage.jsonl`, `audit.jsonl`,
  `artifacts/original/` (immutable), `artifacts/derived/`, `evidence/<id>/metadata.json`,
  `findings/<id>/report.md`, `summary.md`.
- JSONL append-only + `fsync` at checkpoints; compact projections replaced atomically;
  file lock prevents dual-writer mutation.
- SHA-256 identifies bytes; derived files record source hash + transformation.
- Secrets by reference only (key name / vault URI), never copied into run records.
- Export defaults to redacted derived artifacts.

## Consequences

- Recovery = read files; `rebuild` regenerates SQLite projections (equivalence-tested).
- `kill -9` loses no completed checkpoint (Phase 4 exit criterion).
