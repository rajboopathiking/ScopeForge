# Contributing

Working name: ScopeForge. License target: Apache-2.0.

## Ground rules

1. **Permission is data, not a checkbox.** Every live action references a policy
   snapshot + exact asset/method decision.
2. **Prompts never override policy.** Model proposes; deterministic code permits/executes.
3. **Target content is untrusted.** Pages/tool output/repo text/MCP descriptions
   never become authority.
4. **One changed variable per experiment.** Rule → baseline → changed variable →
   evidence → conclusion.
5. Small, typed, tested PRs. Generated protocol code is checked in; never hand-edit
   generated files (run `pnpm generate:protocol`).

## Setup

```bash
pnpm install --frozen-lockfile
uv sync --frozen --all-extras
pnpm generate:protocol
pnpm test
uv run pytest
```

Requires: Node current LTS, Python 3.12+, pnpm, uv. See `docs/development.md`.

## Issue format (plan.md §19)

Each engineering issue must include: testable acceptance criteria, security impact,
docs impact, migration impact.

## Dependency rules (plan.md §12)

- `domain` imports no provider/UI/DB/HTTP/tool implementation.
- `policy` depends only on domain types + pure normalization helpers.
- Executors require an approved capability from policy.
- Providers never call tools directly.
- UI never duplicates policy logic.
- Curriculum content never receives live credentials / unrestricted tools.

## Tests before merge

- Unit + property + contract + provider replay (no external creds) + integration.
- Policy false-allow count must be zero in release-gate corpus.
- Secret-leak count target zero (logs, model context, UI, crashes, exports).
