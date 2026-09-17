# ADR-05: Provider strategy — capability negotiation, OpenAI-compatible first

Date: 2026-09-16. Status: accepted. Implements plan.md §8.

- Canonical `ModelProvider` interface (list_models/probe/stream/count_tokens/close);
  normalized capabilities: streaming, tool calling, parallel tools, strict JSON,
  vision, reasoning controls, caching, limits, usage/pricing, cancellation.
- Tier 1 (MVP): generic OpenAI-compatible Chat/Responses + DeepSeek preset +
  generic Anthropic-compatible + local mock. Tier 2 (beta): native OpenAI Responses,
  Anthropic Messages, Gemini, Ollama. Tier 3: vLLM/LM Studio/OpenRouter/Azure/Bedrock/Vertex.
  Tier 4: optional LiteLLM bridge (long tail). DeepSeek is Tier 1 (OpenAI-compatible).
- Routing on required capabilities + allowlist/denylist + local-only + cost/latency +
  context + task class + health. No cross-provider fallback without user approval;
  never silently downgrade strict-JSON/tool-calling; record adapter version/usage/reason.
- Secrets: OS keychain preferred; env/secret-command references allowed; never log or
  persist secrets; redact auth headers/cookies/tokens before model context.
- `provider test` reports success + capabilities + safe diagnostic codes only.
