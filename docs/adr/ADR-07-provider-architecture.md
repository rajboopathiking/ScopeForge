# ADR-07: Canonical provider interface; frameworks only as Tier-4 bridges

Date: 2026-09-17. Status: accepted. Implements plan.md §8 + Phase 3.

## Context

Should the harness adopt LangChain/LangGraph for orchestration or model access?

## Decision

1. **Core stays framework-free.** The agent runtime is a deterministic,
   policy-gated loop over a canonical `ModelProvider` interface
   (`list_models/probe/stream/count_tokens/close`) with normalized
   capabilities, events, errors, and usage. Rationale:
   - The safety property "model proposes; deterministic code permits and
     executes" must not hide behind framework indirection (e.g. ReAct agents
     that execute tools inside the graph).
   - No ChatModel abstraction negotiates capabilities the way §8 requires
     (strict-JSON degradation, reasoning controls, usage/cost, server-vs-client
     tools); adapter work is unavoidable either way.
   - LangGraph checkpoint state is opaque; our run store is portable
     files + rebuildable SQLite by design (ADR-03).
   - Minimal dependencies serve the SBOM / supply-chain story (§16–17).
2. **Frameworks are welcome as Tier-4 bridge adapters** (like the LiteLLM
   bridge): a `LangChainChatModel → canonical events` adapter, sandboxed,
   capability-probed, and routing-constrained like any other provider.
3. **Python packaging stays single-distribution** (`services/engine`) until the
   Phase 8 plugin SDK needs a second distribution; the `python/*` namespace
   split from plan §12 is deferred, not abandoned.

## Consequences

- Provider breadth comes from small, auditable adapters + replay fixtures.
- If Phase 6 orchestration starts needing graph-style retries/fan-out, revisit
  with policy as explicit middleware — interface unchanged.
