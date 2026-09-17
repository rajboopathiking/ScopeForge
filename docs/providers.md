# Providers (plan.md §8)

ScopeForge normalizes streaming text, structured output, tool calls, reasoning
controls, images, token usage, and errors behind one canonical interface
(`services/engine/src/scopeforge_engine/models/`). Models are chosen by
**required capabilities**, never by brand assumptions.

## Tiers (all implemented)

| Provider id | Endpoint class | Strict JSON | Tools | Reasoning | Listing | Transport |
|---|---|---|---|---|---|---|
| `mock` | local-mock (no network) | native | yes | yes | yes | local |
| `openai-compat` | openai-chat | native | yes | via `provider_options` | yes (`GET /models`) | hosted |
| `deepseek` | openai-chat preset (`https://api.deepseek.com`) | native | yes, except `deepseek-reasoner` | `reasoning_content` → `reasoning.delta` | yes | hosted |
| `anthropic-compat` | anthropic-messages | **via forced tool** (`structured_via="forced-tool"`, recorded) | yes | thinking passthrough | no list endpoint | hosted |
| `gemini` | google-gemini | via forced tool | yes | no | yes (`GET /v1beta/models`) | hosted |
| `ollama` | ollama-chat | no (format=json only) | yes | no | yes (`GET /api/tags`) | local |
| `vllm` / `lmstudio` | openai-chat (preset) | native | yes | no | yes | local |
| `openrouter` / `azure-openai` | openai-chat (preset) | native | yes | no | yes | hosted |
| `litellm` | litellm | via forced tool | yes | no | no | hosted (bridge) |

`litellm` is Tier 4 (`uv sync --extra litellm` or gateway via `openai-compat`).

DeepSeek is Tier 1 because its API is OpenAI-compatible. Provider-specific
controls (e.g. `reasoning_effort`) pass through namespaced `provider_options`
and are shown by the model inspector.

## Strict JSON semantics

- `strict_structured_output: true` → native `json_schema` enforcement.
- Otherwise, if the adapter offers `structured_via_tool`, strict requests are
  served through a forced single tool. The mechanism is **recorded** on the
  `done` event (`structured_via`) and in the routing decision — never silent.
- `require_native_strict` routing (report writing, proof extraction) blocks
  visibly instead of downgrading.
- A strict-incapable endpoint blocks **before any network I/O**.

## Credentials (references only)

`provider add --name N --type T --base-url U --credential REF` where REF is
`env:VAR` (preferred), `keychain:service/account` (OS store), or `cmd:program`
(explicit config, no shell). Values are never stored in `providers.toml`,
never logged, never enter transcripts or run records. `provider test` reports
only presence (`set`/`missing`), declared capabilities, and reachability.

There is deliberately **no `--api-key` flag**: secret values cannot enter shell
history via this CLI.

## Privacy

- `local_only` routing pins to `transport: "local"` providers (mock today,
  Ollama in beta). No cross-provider fallback without an explicit allowlist.
- Authorization headers, cookies, tokens, and credential shapes are redacted
  before any diagnostic, fixture, or model-context construction.
- `provider test --no-live` checks everything except network reachability.

## Custom endpoints

Any OpenAI-compatible base URL works (`openai-compat` + `--base-url`), as does
any Anthropic-compatible one. Capability mismatches degrade visibly
(`CapabilityUnsupported`) instead of guessing from model names.

## Record / replay

`packages/provider-fixtures/*.jsonl` hold sanitized exchanges (regenerate with
`uv run python scripts/make-provider-fixtures.py`). The conformance suite runs
the same golden turn against both hosted shapes plus the mock — no credentials,
no network. `scan_fixture_file` fails closed on secret shapes.

## Framework bridges (LangChain et al.)

Policy (2026-09-17): agent frameworks may appear **only as Tier-4 bridge
adapters** behind the canonical interface — never as the orchestration core.
Rationale in ADR-07. The deterministic policy-gated loop stays framework-free.
