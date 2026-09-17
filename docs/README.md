# Docs Index — ScopeForge

> Every doc is checked in and versioned with the code. Start with `setup.md` for the full quickstart.

| Doc | What it covers | Source of truth |
|---|---|---|
| **[setup.md](setup.md)** | Prerequisites, quickstart, layout, daily loop, CLI, lab, providers, verification, packaging, troubleshooting | This file + `README.md` |
| [architecture.md](architecture.md) | Two-process model, JSON-RPC stdio, run store, dependency rules, diagrams | `apps/terminal` + `services/engine` + `packages/protocol-schema` |
| [threat-model.md](threat-model.md) | Assets, actors, trust boundaries, abuse cases, mitigations | `plan.md` §16 + `services/engine/src/scopeforge_engine/policy.py` |
| [authorization.md](authorization.md) | Modes (`plan`/`artifacts`/`live`), 12-step pipeline, scope matcher, budgets, capabilities | `services/engine/src/scopeforge_engine/policy.py` |
| [protocol.md](protocol.md) | NDJSON stdio, envelope, methods/events, ordering, reconnect, backpressure, cancellation | `packages/protocol-schema/protocol.json` |
| [development.md](development.md) | Dev workflow, regeneration, layout, Python/TS conventions | `scripts/generate-protocol.mjs` + `pyproject.toml` |
| [providers.md](providers.md) | Capability matrix (mock → LiteLLM), strict semantics, credentials, privacy | `services/engine/src/scopeforge_engine/models/` |
| [tools.md](tools.md) | Risk classes, approvals, sandbox, wrapper SDK, MCP, HAR/Burp/brower | `services/engine/src/scopeforge_engine/tools.py` + `mcp.py` |
| [evidence.md](evidence.md) | Originals vs derived, HTTP evidence, redaction, reports, closeout | `services/engine/src/scopeforge_engine/evidence.py` + `reports.py` |
| [packaging.md](packaging.md) | Install (npm/pipx/uv), launcher verification, SBOM/provenance, offline | `scripts/launcher.mjs` + `scripts/sbom.py` |
| [methodology.md](methodology.md) | T01–T12, concrete cases, validation, closeout | `schemas/case.schema.json` + `curriculum/coverage-catalog.json` |
| [learning.md](learning.md) | Curriculum, hints, rubric, lab authoring | `services/engine/src/scopeforge_engine/coach.py` |
| [ADR-01 … ADR-07](adr/) | Process boundary, protocol, run store, UI, provider strategy, sandbox, provider architecture | `docs/adr/*.md` |

**Plan (single source):** [`../plan.md`](../plan.md) — 23 sections, 1312 lines, implementation-ready. All docs above are projections of it.

**Verification (copy-paste):**

```bash
pnpm install --frozen-lockfile
uv sync --python 3.12 --all-extras --all-packages
pnpm generate:protocol
pnpm lint && pnpm test
uv run pytest services/engine/tests -q
python -m pytest services/engine/tests -q
node scripts/generate-protocol.mjs --check
uv run python scripts/sbom.py && ls -lh sbom.json provenance.json
```

**Repo map:** see `setup.md` §3 and `plan.md` §12.
