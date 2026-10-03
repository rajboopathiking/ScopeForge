"""Generic Anthropic-compatible adapter (Tier 1, Phase 3).

Messages SSE: text deltas, tool_use blocks with input-JSON deltas, thinking
deltas, usage. No native strict-JSON mode: strict requests are served via a
forced single tool (`structured_via="forced-tool"`), recorded on the done
event and subject to router approval — never silent.
"""
from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Any

import httpx

from .errors import CapabilityUnsupported
from .http import send_with_retry
from .secrets import CredentialRef, resolve
from .types import (
    ADAPTER_VERSION,
    CanonicalRequest,
    ImageBlock,
    ModelCapabilities,
    ModelDescriptor,
    ModelEvent,
    TextBlock,
    TokenEstimate,
    ToolSpec,
    estimate_tokens,
)

ANTHROPIC_VERSION = "2023-06-01"
DEFAULT_TIMEOUT_S = 60.0
FORCED_TOOL_NAME = "structured_result"


def encode_messages(request: CanonicalRequest) -> tuple[str, list[dict[str, Any]]]:
    system_parts: list[str] = []
    messages: list[dict[str, Any]] = []
    for m in request.messages:
        if m.role == "system":
            system_parts.append("".join(b.text for b in m.content if isinstance(b, TextBlock)))
            continue
        if m.role == "tool":
            messages.append({"role": "user", "content": [{
                "type": "tool_result", "tool_use_id": m.tool_call_id or "",
                "content": "".join(b.text for b in m.content if isinstance(b, TextBlock))}]})
            continue
        blocks: list[dict[str, Any]] = []
        for b in m.content:
            if isinstance(b, TextBlock):
                blocks.append({"type": "text", "text": b.text})
            elif isinstance(b, ImageBlock):
                blocks.append({"type": "image", "source": {"type": "url", "url": b.url}})
        if m.tool_calls:
            for t in m.tool_calls:
                args = t.arguments if isinstance(t.arguments, dict) else _safe_json(t.arguments)
                blocks.append({"type": "tool_use", "id": t.id, "name": t.name, "input": args})
        messages.append({"role": "assistant" if m.role == "assistant" else "user", "content": blocks})
    return "\n".join(system_parts), messages


def _safe_json(raw: str) -> dict[str, Any]:
    try:
        parsed = json.loads(raw)
        return parsed if isinstance(parsed, dict) else {}
    except json.JSONDecodeError:
        return {}


def encode_tools(tools: list[ToolSpec]) -> list[dict[str, Any]]:
    return [{"name": t.name, "description": t.description,
             "input_schema": t.parameters or {"type": "object"}} for t in tools]


class AnthropicCompatAdapter:
    adapter_name = "anthropic-compat"
    endpoint_class = "anthropic-messages"

    def __init__(
        self,
        *,
        base_url: str = "https://api.anthropic.com",
        credential: CredentialRef | None = None,
        provider_name: str = "anthropic-compat",
        supports_tools: bool = True,
        supports_vision: bool = True,
        max_context: int | None = 200000,
        transport_override: httpx.AsyncBaseTransport | None = None,
        timeout_s: float = DEFAULT_TIMEOUT_S,
        retries: int = 2,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.credential = credential
        self.provider_name = provider_name
        self.supports_tools = supports_tools
        self.supports_vision = supports_vision
        self.max_context = max_context
        self._transport = transport_override
        self.timeout_s = timeout_s
        self.retries = retries
        self._client: httpx.AsyncClient | None = None
        self.attempts = 0

    def _headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json", "Accept": "text/event-stream",
                   "anthropic-version": ANTHROPIC_VERSION}
        if self.credential is not None:
            headers["x-api-key"] = resolve(self.credential, self.provider_name)
        return headers

    def _client_for(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(
                base_url=self.base_url, timeout=self.timeout_s, transport=self._transport)
        return self._client

    async def list_models(self) -> list[ModelDescriptor]:
        # No list endpoint in the Anthropic shape: declared, not guessed.
        return []

    async def probe(self, model: str, *, verify_reachability: bool = False) -> ModelCapabilities:
        caps = ModelCapabilities(
            provider=self.provider_name, model=model, adapter=self.adapter_name,
            adapter_version=ADAPTER_VERSION, endpoint_class=self.endpoint_class,
            tool_calling=self.supports_tools, parallel_tools=self.supports_tools,
            strict_structured_output=False, structured_via_tool=self.supports_tools,
            vision=self.supports_vision, model_listing=False, max_context=self.max_context,
        )
        if verify_reachability:
            # Cheapest authenticated shape check: count-tokens endpoint is not
            # universal, so this intentionally stays declared until a turn runs.
            caps = ModelCapabilities(**{**caps.__dict__, "verified_live": False})
        return caps

    async def count_tokens(self, request: CanonicalRequest) -> TokenEstimate:
        return estimate_tokens(request)

    async def close(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    def build_body(self, request: CanonicalRequest) -> tuple[dict[str, Any], bool]:
        """Returns (body, structured_via_forced_tool)."""
        if request.tools and not self.supports_tools:
            raise CapabilityUnsupported("endpoint does not support tool use", self.provider_name)
        strict = request.response_format.kind in ("json", "json_schema") and request.response_format.strict
        system, messages = encode_messages(request)
        tools = list(request.tools)
        forced = False
        tool_choice: Any = "auto"
        if strict:
            if not self.supports_tools:
                raise CapabilityUnsupported(
                    "strict structured output needs tool use on this endpoint; "
                    "choose a strict-capable model (no silent downgrade)", self.provider_name)
            schema = request.response_format.schema or {"type": "object"}
            tools = [*tools, ToolSpec(name=FORCED_TOOL_NAME,
                                      description="Return the strict result object.",
                                      parameters=schema)]
            tool_choice = {"type": "tool", "name": FORCED_TOOL_NAME}
            forced = True
        body: dict[str, Any] = {
            "model": request.model, "messages": messages, "stream": True,
            "max_tokens": request.max_tokens or 1024,
        }
        if system:
            body["system"] = system
        if tools:
            body["tools"] = encode_tools(tools)
            body["tool_choice"] = tool_choice
        if request.temperature is not None:
            body["temperature"] = request.temperature
        return body, forced

    async def stream(self, request: CanonicalRequest) -> AsyncIterator[ModelEvent]:
        body, forced = self.build_body(request)
        client = self._client_for()

        def build() -> httpx.Request:
            return httpx.Request("POST", f"{self.base_url}/messages",
                                 headers=self._headers(), json=body)

        self.attempts = 0

        def counting() -> httpx.Request:
            self.attempts += 1
            return build()

        response = await send_with_retry(client, counting,
                                         provider=self.provider_name, retries=self.retries)
        try:
            async for event in self._parse_sse(response, request, forced):
                yield event
        finally:
            await response.aclose()

    async def _parse_sse(
        self, response: httpx.Response, request: CanonicalRequest, forced: bool,
    ) -> AsyncIterator[ModelEvent]:
        current_event = ""
        blocks: dict[int, dict[str, Any]] = {}
        stop_reason = ""
        model = request.model
        usage_in = usage_out = 0

        async for line in response.aiter_lines():
            if line.startswith("event:"):
                current_event = line[6:].strip()
                continue
            if not line.startswith("data:"):
                continue
            payload = line[5:].strip()
            try:
                data = json.loads(payload)
            except json.JSONDecodeError:
                continue
            if current_event == "message_start":
                model = data.get("message", {}).get("model", model)
                usage = data.get("message", {}).get("usage", {})
                usage_in += int(usage.get("input_tokens", 0))
            elif current_event == "content_block_start":
                index = int(data.get("index", 0))
                block = data.get("content_block", {})
                if block.get("type") == "tool_use":
                    blocks[index] = {"id": block.get("id", ""), "name": block.get("name", ""),
                                     "json": ""}
                    yield ModelEvent(kind="tool.begin", tool_id=blocks[index]["id"],
                                     tool_name=blocks[index]["name"], model=model)
                else:
                    blocks[index] = {"id": "", "name": "", "json": ""}
            elif current_event == "content_block_delta":
                index = int(data.get("index", 0))
                delta = data.get("delta", {})
                dtype = delta.get("type", "")
                if dtype == "text_delta" and delta.get("text"):
                    yield ModelEvent(kind="text.delta", text=delta["text"], model=model)
                elif dtype == "input_json_delta" and delta.get("partial_json"):
                    slot = blocks.setdefault(index, {"id": "", "name": "", "json": ""})
                    slot["json"] += delta["partial_json"]
                    yield ModelEvent(kind="tool.args_delta", tool_id=slot["id"],
                                     tool_name=slot["name"],
                                     tool_args_delta=delta["partial_json"], model=model)
                elif dtype == "thinking_delta" and delta.get("thinking"):
                    yield ModelEvent(kind="reasoning.delta", reasoning=delta["thinking"],
                                     model=model)
            elif current_event == "message_delta":
                delta = data.get("delta", {})
                if delta.get("stop_reason"):
                    stop_reason = str(delta["stop_reason"])
                usage = data.get("usage", {})
                usage_out += int(usage.get("output_tokens", 0))
            elif current_event == "message_stop":
                pass
            elif current_event == "error":
                yield ModelEvent(kind="error", model=model,
                                 error=f"provider error: {data.get('error', data)}")
                return
        for index in sorted(blocks):
            slot = blocks[index]
            if not slot["name"]:
                continue
            try:
                parsed = json.loads(slot["json"]) if slot["json"] else {}
            except json.JSONDecodeError as exc:
                yield ModelEvent(kind="error", model=model, tool_id=slot["id"],
                                 tool_name=slot["name"],
                                 error=f"malformed tool input for {slot['name']!r}: {exc}")
                continue
            yield ModelEvent(kind="tool.done", model=model, tool_id=slot["id"],
                             tool_name=slot["name"], tool_arguments=parsed)
        if usage_in or usage_out:
            yield ModelEvent(kind="usage", model=model,
                             input_tokens=usage_in, output_tokens=usage_out)
        yield ModelEvent(kind="done", model=model, stop_reason=stop_reason or "end_turn",
                         structured_via="forced-tool" if forced else "")
