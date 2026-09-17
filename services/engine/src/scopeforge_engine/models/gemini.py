"""Native Gemini adapter (Phase 8, Tier 2).

Google Gemini function calling (https://ai.google.dev/gemini-api/docs/function-calling)
uses a distinct REST shape:
  POST /v1beta/models/{model}:streamGenerateContent?key=...  (or header x-goog-api-key)
  body: { contents: [{role, parts}], tools: [{functionDeclarations}], generationConfig }
Streaming is SSE-like JSON array chunks. Normalized to canonical events.
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

DEFAULT_TIMEOUT_S = 60.0
DEFAULT_TIMEOUT_S = 60.0


def _to_gemini_parts(msg_content: list) -> list[dict[str, Any]]:
    parts: list[dict[str, Any]] = []
    for b in msg_content:
        if isinstance(b, TextBlock):
            parts.append({"text": b.text})
        elif isinstance(b, ImageBlock):
            # Gemini expects inlineData or fileData; we pass image url as text fallback
            # unless it's a data: URI (handled by caller to inline).
            if b.url.startswith("data:"):
                # data:image/png;base64,xxx
                try:
                    header, data = b.url.split(",", 1)
                    mime = header.split(":")[1].split(";")[0]
                    parts.append({"inlineData": {"mimeType": mime, "data": data}})
                except ValueError:
                    parts.append({"text": b.url})
            else:
                parts.append({"text": f"[image: {b.url}]"})
    return parts


def encode_contents(messages: list) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for m in messages:
        if m.role == "system":
            # Gemini has systemInstruction separate; fold into first user for simplicity
            out.append({"role": "user", "parts": _to_gemini_parts(m.content)})
            continue
        if m.role == "tool":
            # Tool result -> functionResponse
            text = "".join(b.text for b in m.content if isinstance(b, TextBlock))
            try:
                response = json.loads(text) if text.strip().startswith("{") else {"result": text}
            except json.JSONDecodeError:
                response = {"result": text}
            out.append({
                "role": "function",
                "parts": [{"functionResponse": {"name": m.name or "unknown", "response": response}}],
            })
            continue
        role = "model" if m.role == "assistant" else "user"
        parts = _to_gemini_parts(m.content)
        # Tool calls from assistant -> functionCall parts
        if m.tool_calls:
            for tc in m.tool_calls:
                args = tc.arguments if isinstance(tc.arguments, dict) else {}
                if isinstance(tc.arguments, str):
                    try:
                        args = json.loads(tc.arguments)
                    except json.JSONDecodeError:
                        args = {}
                parts.append({"functionCall": {"name": tc.name, "args": args}})
        if parts:
            out.append({"role": role, "parts": parts})
    return out


def encode_function_declarations(tools: list) -> list[dict[str, Any]] | None:
    if not tools:
        return None
    return [{
        "name": t.name,
        "description": t.description,
        "parameters": t.parameters or {"type": "object"},
    } for t in tools]


class GeminiAdapter:
    adapter_name = "gemini"
    endpoint_class = "google-gemini"

    def __init__(
        self,
        *,
        credential: CredentialRef | None = None,
        provider_name: str = "gemini",
        base_url: str = "https://generativelanguage.googleapis.com",
        transport_override: httpx.AsyncBaseTransport | None = None,
        timeout_s: float = DEFAULT_TIMEOUT_S,
        retries: int = 2,
        api_version: str = "v1beta",
    ) -> None:
        self.credential = credential
        self.provider_name = provider_name
        self.base_url = base_url.rstrip("/")
        self.api_version = api_version
        self._transport = transport_override
        self.timeout_s = timeout_s
        self.retries = retries
        self._client: httpx.AsyncClient | None = None
        self.attempts = 0

    def _headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if self.credential is not None:
            # Gemini uses x-goog-api-key header; also supports ?key= query param
            headers["x-goog-api-key"] = resolve(self.credential, self.provider_name)
        return headers

    def _client_for(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=self.timeout_s, transport=self._transport)
        return self._client

    async def list_models(self) -> list[ModelDescriptor]:
        client = self._client_for()
        url = f"{self.base_url}/{self.api_version}/models"
        request = httpx.Request("GET", url, headers=self._headers())
        try:
            resp = await client.send(request)
            resp.raise_for_status()
            data = resp.json()
            await resp.aclose()
        except Exception as exc:
            raise classify_http_error(self.provider_name, exc, attempts=1) from None
        # Gemini returns {models: [{name: "models/gemini-1.5-pro", ...}]}
        models = data.get("models", data.get("data", []))
        out = []
        for m in models:
            name = m.get("name", m.get("id", ""))
            # Strip prefix "models/"
            short = name.split("/")[-1] if "/" in name else name
            if short:
                out.append(ModelDescriptor(id=short, provider=self.provider_name))
        return out

    async def probe(self, model: str, *, verify_reachability: bool = False) -> ModelCapabilities:
        caps = ModelCapabilities(
            provider=self.provider_name, model=model, adapter=self.adapter_name,
            adapter_version=ADAPTER_VERSION, endpoint_class=self.endpoint_class,
            transport="hosted",
            tool_calling=True, parallel_tools=True,
            strict_structured_output=False, structured_via_tool=True,
            vision=True, reasoning=False,
            model_listing=True, max_context=1000000,
        )
        if verify_reachability:
            try:
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
                "Gemini strict JSON is via forced function declaration (structured_via_tool); "
                "use native strict models for strict=true", self.provider_name)
        body: dict[str, Any] = {
            "contents": encode_contents(request.messages),
        }
        tools = encode_function_declarations(request.tools)
        if tools:
            body["tools"] = [{"functionDeclarations": tools}]
            body["toolConfig"] = {"functionCallingConfig": {"mode": "AUTO"}}
        # Strict via forced tool
        if request.response_format.kind in ("json", "json_schema") and request.response_format.strict is False:
            pass
        # For templated forced-tool strict, inject schema as function
        if request.response_format.kind in ("json", "json_schema") and request.response_format.strict:
            # Unreachable due to above, but keep for future explicit allow
            schema = request.response_format.schema or {"type": "object"}
            body.setdefault("tools", []).append(
                {"functionDeclarations": [{"name": "structured_result", "parameters": schema}]})
        config: dict[str, Any] = {}
        if request.max_tokens is not None:
            config["maxOutputTokens"] = request.max_tokens
        if request.temperature is not None:
            config["temperature"] = request.temperature
        if request.response_format.kind == "json":
            config["responseMimeType"] = "application/json"
        if config:
            body["generationConfig"] = config
        return body

    async def stream(self, request: CanonicalRequest) -> AsyncIterator[ModelEvent]:
        body = self.build_body(request)
        client = self._client_for()
        url = f"{self.base_url}/{self.api_version}/models/{request.model}:streamGenerateContent"

        def build() -> httpx.Request:
            self.attempts += 1
            return httpx.Request("POST", url, headers=self._headers(), json=body)

        response = await send_with_retry(client, build, provider=self.provider_name, retries=self.retries)
        try:
            async for ev in self._parse_stream(response, request):
                yield ev
        finally:
            await response.aclose()

    async def _parse_stream(self, response: httpx.Response, request: CanonicalRequest) -> AsyncIterator[ModelEvent]:
        model = request.model
        usage_in = usage_out = 0
        async for line in response.aiter_lines():
            line = line.strip()
            if not line or line == "[" or line == "]" or line == ",":
                continue
            # Gemini streams JSON objects per line, sometimes comma-terminated
            if line.endswith(","):
                line = line[:-1]
            try:
                chunk = json.loads(line)
            except json.JSONDecodeError:
                continue
            # Handle array wrapper
            if isinstance(chunk, list):
                for item in chunk:
                    async for ev in self._handle_candidate(item, model):
                        yield ev
                continue
            async for ev in self._handle_candidate(chunk, model):
                yield ev
            # Usage
            usage = chunk.get("usageMetadata", {})
            if usage:
                usage_in = usage.get("promptTokenCount", usage_in)
                usage_out = usage.get("candidatesTokenCount", usage_out)
        if usage_in or usage_out:
            yield ModelEvent(kind="usage", model=model, input_tokens=usage_in, output_tokens=usage_out)
        yield ModelEvent(kind="done", model=model, stop_reason="stop")

    async def _handle_candidate(self, chunk: dict, model: str) -> AsyncIterator[ModelEvent]:
        candidates = chunk.get("candidates", [chunk] if "content" in chunk else [])
        for cand in candidates:
            content = cand.get("content", {})
            parts = content.get("parts", [])
            for part in parts:
                if "text" in part and part["text"]:
                    yield ModelEvent(kind="text.delta", text=part["text"], model=model)
                if "functionCall" in part:
                    fc = part["functionCall"]
                    name = fc.get("name", "")
                    args = fc.get("args", {})
                    tool_id = f"call_{name}_{hash(json.dumps(args, sort_keys=True)) & 0xffff}"
                    yield ModelEvent(kind="tool.begin", tool_id=tool_id, tool_name=name, model=model)
                    if args:
                        delta = json.dumps(args)
                        yield ModelEvent(kind="tool.args_delta", tool_id=tool_id, tool_name=name,
                                         tool_args_delta=delta, model=model)
                    yield ModelEvent(kind="tool.done", model=model, tool_id=tool_id,
                                     tool_name=name, tool_arguments=args)
            reason = cand.get("finishReason")
            if reason and reason != "STOP":
                yield ModelEvent(kind="reasoning.delta", reasoning=f"finishReason={reason}", model=model)
