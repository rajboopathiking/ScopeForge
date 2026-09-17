"""Generic OpenAI-compatible adapter + DeepSeek preset (Tier 1, Phase 3).

Covers Chat Completions streaming (SSE): text deltas, indexed tool-call
accumulation, `reasoning_content` (DeepSeek), usage via `stream_options`,
strict `response_format: json_schema`. Responses-class endpoints are Tier 2.
"""
from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Any

import httpx

from .errors import CapabilityUnsupported, ProviderError
from .http import classify_http_error, send_with_retry
from .secrets import CredentialRef, resolve
from .types import (
    ADAPTER_VERSION,
    CanonicalRequest,
    ImageBlock,
    Message,
    ModelCapabilities,
    ModelDescriptor,
    ModelEvent,
    TextBlock,
    TokenEstimate,
    estimate_tokens,
)

DEFAULT_TIMEOUT_S = 60.0


def _text_of(blocks: list) -> str:
    return "".join(b.text for b in blocks if isinstance(b, TextBlock))


def encode_messages(messages: list[Message]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for m in messages:
        if m.role == "tool":
            out.append({"role": "tool", "tool_call_id": m.tool_call_id or "",
                        "content": _text_of(m.content)})
            continue
        content: list[dict[str, Any]] = []
        for b in m.content:
            if isinstance(b, TextBlock):
                content.append({"type": "text", "text": b.text})
            elif isinstance(b, ImageBlock):
                content.append({"type": "image_url", "image_url": {"url": b.url, "detail": b.detail}})
        msg: dict[str, Any] = {"role": m.role, "content": content}
        if m.tool_calls:
            msg["tool_calls"] = [
                {"id": t.id, "type": "function",
                 "function": {"name": t.name, "arguments": t.arguments
                              if isinstance(t.arguments, str) else json.dumps(t.arguments)}}
                for t in m.tool_calls
            ]
        if m.name:
            msg["name"] = m.name
        out.append(msg)
    return out


def encode_tools(request: CanonicalRequest) -> list[dict[str, Any]] | None:
    if not request.tools:
        return None
    return [{"type": "function", "function": {
        "name": t.name, "description": t.description, "parameters": t.parameters or {"type": "object"}}}
        for t in request.tools]


class OpenAICompatAdapter:
    """Generic adapter. Subclass/preset for provider defaults + quirks."""

    adapter_name = "openai-compat"
    endpoint_class = "openai-chat"

    def __init__(
        self,
        *,
        base_url: str,
        credential: CredentialRef | None = None,
        provider_name: str = "openai-compat",
        transport: str = "hosted",
        default_models: list[str] | None = None,
        supports_tools: bool = True,
        supports_strict: bool = True,
        supports_reasoning: bool = False,
        supports_vision: bool = True,
        max_context: int | None = None,
        transport_override: httpx.AsyncBaseTransport | None = None,
        timeout_s: float = DEFAULT_TIMEOUT_S,
        retries: int = 2,
        auth_header: str = 'Authorization',
        auth_scheme: str = 'Bearer',
        extra_query: dict | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.credential = credential
        self.provider_name = provider_name
        self.transport_kind = transport
        self.default_models = default_models or []
        self.supports_tools = supports_tools
        self.supports_strict = supports_strict
        self.supports_reasoning = supports_reasoning
        self.supports_vision = supports_vision
        self.max_context = max_context
        self._transport = transport_override
        self.timeout_s = timeout_s
        self.retries = retries
        self._client: httpx.AsyncClient | None = None
        self._probe_cache: dict[str, tuple[ModelCapabilities, float]] = {}
        self.auth_header = auth_header
        self.auth_scheme = auth_scheme
        self.extra_query = extra_query or {}
        self.attempts = 0  # test hook: HTTP attempts for the last stream()

    def _headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json", "Accept": "text/event-stream"}
        if self.credential is not None:
            value = resolve(self.credential, self.provider_name)
            headers[self.auth_header] = f"{self.auth_scheme} {value}" if self.auth_scheme else value
        return headers

    def _client_for(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(
                base_url=self.base_url, timeout=self.timeout_s, transport=self._transport)
        return self._client

    # -- ModelProvider interface --

    async def list_models(self) -> list[ModelDescriptor]:
        client = self._client_for()
        request = httpx.Request(
            "GET", f"{self.base_url}/models",
            headers=self._headers(),
            params=self.extra_query or None,
        )
        try:
            response = await client.send(request)
            response.raise_for_status()
            data = response.json()
            await response.aclose()
        except Exception as exc:
            raise classify_http_error(self.provider_name, exc, attempts=1) from None
        return [ModelDescriptor(id=str(m.get("id", "")), provider=self.provider_name)
                for m in data.get("data", []) if m.get("id")]

    async def probe(self, model: str, *, verify_reachability: bool = False) -> ModelCapabilities:
        import time

        cached = self._probe_cache.get(model)
        if cached and time.monotonic() - cached[1] < 300:
            hit, _ = cached
            # A cached non-live probe never satisfies a live verification request.
            if hit.verified_live or not verify_reachability:
                return hit
        caps = ModelCapabilities(
            provider=self.provider_name, model=model, adapter=self.adapter_name,
            adapter_version=ADAPTER_VERSION, endpoint_class=self.endpoint_class,
            transport="hosted" if self.transport_kind == "hosted" else "local",
            tool_calling=self.supports_tools and self._model_supports_tools(model),
            parallel_tools=self.supports_tools,
            strict_structured_output=self.supports_strict,
            vision=self.supports_vision,
            reasoning=self.supports_reasoning or self._model_has_reasoning(model),
            model_listing=True, max_context=self.max_context,
        )
        if verify_reachability:
            try:
                await self.list_models()
                caps = ModelCapabilities(**{**caps.__dict__, "verified_live": True})
            except ProviderError:
                raise
        self._probe_cache[model] = (caps, time.monotonic())
        return caps

    def _model_supports_tools(self, model: str) -> bool:  # noqa: ARG002
        return True

    def _model_has_reasoning(self, model: str) -> bool:  # noqa: ARG002
        return False

    async def count_tokens(self, request: CanonicalRequest) -> TokenEstimate:
        return estimate_tokens(request)

    async def close(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    def build_body(self, request: CanonicalRequest) -> dict[str, Any]:
        if request.tools and not self.supports_tools:
            raise CapabilityUnsupported("endpoint does not support tool calling", self.provider_name)
        strict = request.response_format.kind in ("json", "json_schema") and request.response_format.strict
        if strict and not self.supports_strict:
            raise CapabilityUnsupported(
                "strict structured output unsupported by this endpoint; "
                "choose a strict-capable model (no silent downgrade)", self.provider_name)
        body: dict[str, Any] = {
            "model": request.model,
            "messages": encode_messages(request.messages),
            "stream": True,
            "stream_options": {"include_usage": True},
        }
        tools = encode_tools(request)
        if tools:
            body["tools"] = tools
            body["tool_choice"] = request.tool_choice
        if strict:
            schema = request.response_format.schema or {"type": "object"}
            body["response_format"] = {
                "type": "json_schema",
                "json_schema": {"name": "result", "schema": schema, "strict": True},
            }
        elif request.response_format.kind == "json":
            body["response_format"] = {"type": "json_object"}
        if request.max_tokens is not None:
            body["max_tokens"] = request.max_tokens
        if request.temperature is not None:
            body["temperature"] = request.temperature
        # Provider-specific controls pass through namespaced options (shown in inspector).
        if request.reasoning and request.reasoning.effort and self.supports_reasoning:
            body["reasoning_effort"] = request.reasoning.effort
        for key in ("reasoning_effort", "reasoning"):
            if key in request.provider_options:
                body[key] = request.provider_options[key]
        return body

    async def stream(self, request: CanonicalRequest) -> AsyncIterator[ModelEvent]:
        body = self.build_body(request)  # raises CapabilityUnsupported before any I/O
        client = self._client_for()

        def build() -> httpx.Request:
            self.attempts += 1
            return httpx.Request(
                "POST", f"{self.base_url}/chat/completions",
                headers=self._headers(), json=body,
                params=self.extra_query or None,
            )

        response = await send_with_retry(client, build,
                                         provider=self.provider_name, retries=self.retries)
        try:
            async for event in self._parse_sse(response, request):
                yield event
        finally:
            await response.aclose()

    async def _parse_sse(
        self, response: httpx.Response, request: CanonicalRequest,
    ) -> AsyncIterator[ModelEvent]:
        tools: dict[int, dict[str, Any]] = {}
        stop_reason = ""
        model = request.model
        async for line in response.aiter_lines():
            if not line.startswith("data:"):
                continue
            payload = line[5:].strip()
            if payload == "[DONE]":
                break
            try:
                chunk = json.loads(payload)
            except json.JSONDecodeError:
                continue  # ignore malformed keepalives; strictness applies to tool args
            model = chunk.get("model", model)
            for choice in chunk.get("choices", []):
                delta = choice.get("delta", {})
                if choice.get("finish_reason"):
                    stop_reason = str(choice["finish_reason"])
                text = delta.get("content")
                if text:
                    yield ModelEvent(kind="text.delta", text=text, model=model)
                reasoning = delta.get("reasoning_content")
                if reasoning:
                    yield ModelEvent(kind="reasoning.delta", reasoning=reasoning, model=model)
                for tc in delta.get("tool_calls", []):
                    index = int(tc.get("index", 0))
                    slot = tools.setdefault(index, {"id": "", "name": "", "args": ""})
                    if tc.get("id"):
                        slot["id"] = tc["id"]
                    fn = tc.get("function", {})
                    if fn.get("name"):
                        slot["name"] = fn["name"]
                        yield ModelEvent(kind="tool.begin", tool_id=slot["id"],
                                         tool_name=slot["name"], model=model)
                    if fn.get("arguments"):
                        slot["args"] += fn["arguments"]
                        yield ModelEvent(kind="tool.args_delta", tool_id=slot["id"],
                                         tool_name=slot["name"],
                                         tool_args_delta=fn["arguments"], model=model)
            usage = chunk.get("usage")
            if usage:
                yield ModelEvent(kind="usage", model=model,
                                 input_tokens=int(usage.get("prompt_tokens", 0)),
                                 output_tokens=int(usage.get("completion_tokens", 0)))
        for index in sorted(tools):
            slot = tools[index]
            try:
                parsed = json.loads(slot["args"]) if slot["args"] else {}
            except json.JSONDecodeError as exc:
                yield ModelEvent(kind="error", model=model, tool_id=slot["id"],
                                 tool_name=slot["name"],
                                 error=f"malformed tool arguments for {slot['name']!r}: {exc}")
                continue
            yield ModelEvent(kind="tool.done", model=model, tool_id=slot["id"],
                             tool_name=slot["name"], tool_arguments=parsed)
        structured_via = "native" if (
            request.response_format.kind in ("json", "json_schema")
            and request.response_format.strict) else ""
        yield ModelEvent(kind="done", model=model, stop_reason=stop_reason or "stop",
                         structured_via=structured_via)


class DeepSeekAdapter(OpenAICompatAdapter):
    """DeepSeek preset: official API is OpenAI-compatible (+ `reasoning_content`).

    deepseek-reasoner does not support tool calls — probe() reflects that so the
    router visibly blocks tool workflows instead of failing mid-stream.
    """

    adapter_name = "deepseek"
    _REASONER = "deepseek-reasoner"

    def __init__(self, *, credential: CredentialRef | None = None,
                 base_url: str = "https://api.deepseek.com", **kw: Any) -> None:
        super().__init__(base_url=base_url, credential=credential, provider_name="deepseek",
                         default_models=["deepseek-chat", self._REASONER],
                         supports_tools=True, supports_strict=True, supports_reasoning=True,
                         max_context=64000, **kw)

    def _model_supports_tools(self, model: str) -> bool:
        return model != self._REASONER

    def _model_has_reasoning(self, model: str) -> bool:
        return model == self._REASONER
