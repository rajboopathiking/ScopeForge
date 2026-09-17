"""Native Ollama adapter (Phase 8, Tier 2).

Local model via Ollama API (https://docs.ollama.com/api/introduction).
POST /api/chat  {model, messages, tools?, format?, stream:true}
Streaming NDJSON: {"message":{"role":"assistant","content":"...","tool_calls":[...]},"done":false}
plus final {"done":true, "prompt_eval_count":..., "eval_count":...}
Transport is local, no auth by default (optional bearer via credential).
"""
from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Any

import httpx

try:
    from .errors import CapabilityUnsupported
    from .http import classify_http_error, send_with_retry
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
        estimate_tokens,
    )
except ImportError:  # direct script execution; script dir is on sys.path
    from errors import CapabilityUnsupported  # type: ignore[no-redef]
    from http import classify_http_error, send_with_retry  # type: ignore[no-redef]
    from secrets import CredentialRef, resolve  # type: ignore[no-redef]
    from types import (  # type: ignore[no-redef]
        ADAPTER_VERSION,
        CanonicalRequest,
        ImageBlock,
        ModelCapabilities,
        ModelDescriptor,
        ModelEvent,
        TextBlock,
        TokenEstimate,
        estimate_tokens,
    )

DEFAULT_TIMEOUT_S = 120.0  # local models can be slow
DEFAULT_TIMEOUT_S = 120.0  # local models can be slow


def encode_messages(messages: list) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for m in messages:
        if m.role == "tool":
            text = "".join(b.text for b in m.content if isinstance(b, TextBlock))
            out.append({"role": "tool", "content": text, "tool_call_id": m.tool_call_id})
            continue
        content_parts: list[dict[str, Any]] = []
        for b in m.content:
            if isinstance(b, TextBlock):
                content_parts.append({"type": "text", "text": b.text})
            elif isinstance(b, ImageBlock):
                # Ollama supports images as base64; pass through url
                content_parts.append({"type": "image_url", "image_url": {"url": b.url}})
        # Ollama expects string content or array; normalize to string for compat
        text = "".join(p["text"] for p in content_parts if "text" in p)
        if m.tool_calls:
            # Ollama tool calls in history
            entry: dict[str, Any] = {"role": "assistant", "content": text}
            entry["tool_calls"] = [
                {"id": tc.id, "function": {"name": tc.name,
                 "arguments": tc.arguments if isinstance(tc.arguments, dict)
                 else json.loads(tc.arguments) if isinstance(tc.arguments, str) and tc.arguments else {}}}
                for tc in m.tool_calls
            ]
            out.append(entry)
        else:
            out.append({"role": m.role if m.role != "system" else "system", "content": text})
    return out


def encode_tools(tools: list) -> list[dict[str, Any]] | None:
    if not tools:
        return None
    return [{"type": "function", "function": {
        "name": t.name, "description": t.description,
        "parameters": t.parameters or {"type": "object"}}}
        for t in tools]


class OllamaAdapter:
    adapter_name = "ollama"
    endpoint_class = "ollama-chat"

    def __init__(
        self,
        *,
        base_url: str = "http://localhost:11434",
        provider_name: str = "ollama",
        credential: CredentialRef | None = None,
        transport_override: httpx.AsyncBaseTransport | None = None,
        timeout_s: float = DEFAULT_TIMEOUT_S,
        retries: int = 1,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.provider_name = provider_name
        self.credential = credential
        self._transport = transport_override
        self.timeout_s = timeout_s
        self.retries = retries
        self._client: httpx.AsyncClient | None = None
        self.attempts = 0

    def _headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if self.credential is not None:
            headers["Authorization"] = f"Bearer {resolve(self.credential, self.provider_name)}"
        return headers

    def _client_for(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=self.timeout_s, transport=self._transport)
        return self._client

    async def list_models(self) -> list[ModelDescriptor]:
        client = self._client_for()
        request = httpx.Request("GET", f"{self.base_url}/api/tags", headers=self._headers())
        try:
            resp = await client.send(request)
            resp.raise_for_status()
            data = resp.json()
            await resp.aclose()
        except Exception as exc:
            raise classify_http_error(self.provider_name, exc, attempts=1) from None
        models = data.get("models", [])
        return [ModelDescriptor(id=m.get("name", ""), provider=self.provider_name)
                for m in models if m.get("name")]

    async def probe(self, model: str, *, verify_reachability: bool = False) -> ModelCapabilities:
        caps = ModelCapabilities(
            provider=self.provider_name, model=model, adapter=self.adapter_name,
            adapter_version=ADAPTER_VERSION, endpoint_class=self.endpoint_class,
            transport="local",
            tool_calling=True, parallel_tools=False,
            strict_structured_output=False, structured_via_tool=False,
            vision=False, reasoning=False,
            model_listing=True, max_context=8192,
        )
        if verify_reachability:
            try:
                # Lightweight check: list models
                await self.list_models()
                caps = ModelCapabilities(**{**caps.__dict__, "verified_live": True})
            except Exception:
                raise
        return caps

    async def count_tokens(self, request: CanonicalRequest) -> TokenEstimate:
        return estimate_tokens(request)

    async def close(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    def build_body(self, request: CanonicalRequest) -> dict[str, Any]:
        if request.response_format.kind in ("json", "json_schema") and request.response_format.strict:
            raise CapabilityUnsupported(
                "Ollama strict JSON requires model-side support; use format=json without strict",
                self.provider_name)
        body: dict[str, Any] = {
            "model": request.model,
            "messages": encode_messages(request.messages),
            "stream": True,
        }
        tools = encode_tools(request.tools)
        if tools:
            body["tools"] = tools
        if request.response_format.kind == "json":
            body["format"] = "json"
        if request.temperature is not None:
            body.setdefault("options", {})["temperature"] = request.temperature
        return body

    async def stream(self, request: CanonicalRequest) -> AsyncIterator[ModelEvent]:
        body = self.build_body(request)
        client = self._client_for()

        def build() -> httpx.Request:
            self.attempts += 1
            return httpx.Request("POST", f"{self.base_url}/api/chat",
                                 headers=self._headers(), json=body)

        response = await send_with_retry(client, build, provider=self.provider_name, retries=self.retries)
        try:
            async for ev in self._parse_ndjson(response, request):
                yield ev
        finally:
            await response.aclose()

    async def _parse_ndjson(self, response: httpx.Response, request: CanonicalRequest) -> AsyncIterator[ModelEvent]:
        model = request.model
        async for line in response.aiter_lines():
            line = line.strip()
            if not line:
                continue
            try:
                chunk = json.loads(line)
            except json.JSONDecodeError:
                continue
            if chunk.get("error"):
                yield ModelEvent(kind="error", model=model, error=str(chunk["error"]))
                return
            msg = chunk.get("message", {})
            content = msg.get("content", "")
            if content:
                yield ModelEvent(kind="text.delta", text=content, model=model)
            for tc in msg.get("tool_calls", []):
                fn = tc.get("function", {})
                name = fn.get("name", "")
                args = fn.get("arguments", {})
                tool_id = tc.get("id", f"call_{name}")
                yield ModelEvent(kind="tool.begin", tool_id=tool_id, tool_name=name, model=model)
                if args:
                    delta = json.dumps(args) if isinstance(args, dict) else str(args)
                    yield ModelEvent(kind="tool.args_delta", tool_id=tool_id, tool_name=name,
                                     tool_args_delta=delta, model=model)
                yield ModelEvent(kind="tool.done", model=model, tool_id=tool_id,
                                 tool_name=name, tool_arguments=args if isinstance(args, dict) else {})
            if chunk.get("done"):
                prompt_eval = chunk.get("prompt_eval_count", 0)
                eval_count = chunk.get("eval_count", 0)
                if prompt_eval or eval_count:
                    yield ModelEvent(kind="usage", model=model,
                                     input_tokens=int(prompt_eval), output_tokens=int(eval_count))
                yield ModelEvent(kind="done", model=model, stop_reason="stop")
                return
        yield ModelEvent(kind="done", model=model, stop_reason="stop")
