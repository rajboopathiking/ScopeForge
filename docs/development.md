# Development

Requires: Node current LTS, Python ≥ 3.12, pnpm ≥ 9, uv ≥ 0.5.
(Note 2026-09-16: local env had Python 3.11 — use `uv python install 3.12` and
`uv sync`; spine itself is stdlib-only so `python3 server.py` still runs.)

```bash
pnpm install --frozen-lockfile
uv sync --frozen --all-extras
pnpm generate:protocol
pnpm test                 # vitest: contract + e2e spine (10k soak, cancel, version reject)
uv run pytest             # engine: handshake, ordering, golden T02 proof gate
```

## Regeneration / drift

Generated files (`packages/protocol-ts/src/generated.ts`,
`services/engine/src/scopeforge_engine/protocol_generated.py`,
`tests/contract/fixtures/envelope.json`) are checked in. CI runs the `--check`
drift gate; never hand-edit generated files.

## Layout

`apps/terminal` (TS CLI shell) · `services/engine` (Python stdio server) ·
`packages/protocol-schema` (source of truth) · `packages/protocol-ts` (types+client) ·
`schemas/` (domain) · `evals/golden/` (artifact-only T02 slice) ·
`tests/{contract,e2e}` · `docs/adr/`.

## Python conventions (AI in Python, UI in TS — ADR-01)

- All agent/policy/tool/evidence logic lives in `services/engine`. The TUI
  never duplicates policy or model logic; it renders engine data.
- Engine modules are **stdlib-importable**: `models/` degrades gracefully
  (`HTTP_AVAILABLE`), and every module uses the dual-import convention
  (package-relative first, `sys.path` fallback) so `python server.py` runs on
  bare interpreters. New modules must follow both rules.
- `services/engine/tests/conftest.py` puts `src/` on `sys.path`; gate
  third-party needs with `pytest.importorskip`.
- Generated protocol bindings are checked in; never hand-edit.

## Dependency rules (enforced in review; import lints land with Phase 4)

domain ← policy ← executors; providers never call tools; UI never duplicates policy.
