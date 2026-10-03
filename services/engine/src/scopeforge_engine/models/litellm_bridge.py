"""Optional LiteLLM bridge (Phase 8, Tier 4).

If `litellm` is installed, this adapter delegates to litellm.acompletion
with streaming, normalizing to canonical events. If not installed, construction
raises CapabilityUnsupported with an actionable message (visible degradation).

Runs in-process, pinned via the project's optional dependency. Caller may also
run LiteLLM as a separately pinned local gateway and point an openai-compat
provider at it — both paths are supported.
"""
from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Any

try:
    from .errors import CapabilityUnsupported
    from .types import (
        ADAPTER_VERSION,
        CanonicalRequest,
        ModelCapabilities,
        ModelDescriptor,
        ModelEvent,
        TextBlock,
        TokenEstimate,
        estimate_tokens,
    )
except ImportError:  # direct script execution; script dir is on sys.path
    from errors import CapabilityUnsupported  # type: ignore[no-redef]
    from types import (  # type: ignore[no-redef]
        ADAPTER_VERSION,
        CanonicalRequest,
        ModelCapabilities,
        ModelDescriptor,
        ModelEvent,
        TextBlock,
        TokenEstimate,
        estimate_tokens,
    )

def _check_litellm():
    try:
        import litellm  # type: ignore[import-not-found]
        return litellm
    except ImportError:
        raise CapabilityUnsupported(
            "LiteLLM bridge not installed; add with `uv sync --extra litellm` "
            "or use an openai-compat provider pointed at a LiteLLM gateway") from None


class LiteLLMBridgeAdapter:
    adapter_name = "litellm-bridge"
    endpoint_class = "litellm"

    def __init__(
        self,
        *,
        model: str = "openai/gpt-4o-mini",
        provider_name: str = "litellm",
        litellm_params: dict[str, Any] | None = None,
    ) -> None:
        self.litellm = _check_litellm()
        self.model = model
        self.provider_name = provider_name
        self.litellm_params = litellm_params or {}
        self.attempts = 0

    async def list_models(self) -> list[ModelDescriptor]:
        # LiteLLM doesn't enumerate; expose the configured model
        return [ModelDescriptor(id=self.model, provider=self.provider_name)]

    async def probe(self, model: str, *, verify_reachability: bool = False) -> ModelCapabilities:
        _ = verify_reachability
        return ModelCapabilities(
            provider=self.provider_name, model=model, adapter=self.adapter_name,
            adapter_version=ADAPTER_VERSION, endpoint_class=self.endpoint_class,
            transport="hosted",
            tool_calling=True, parallel_tools=True,
            strict_structured_output=False, structured_via_tool=True,
            vision=False, reasoning=False,
            model_listing=False, max_context=None,
        )

    async def count_tokens(self, request: CanonicalRequest) -> TokenEstimate:
        return estimate_tokens(request)

    async def close(self) -> None:
        pass

    def _to_litellm_messages(self, request: CanonicalRequest) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for m in request.messages:
            text = "".join(b.text for b in m.content if isinstance(b, TextBlock))
            if m.role == "tool":
                out.append({"role": "tool", "tool_call_id": m.tool_call_id, "content": text})
            elif m.tool_calls:
                out.append({
                    "role": "assistant", "content": text or None,
                    "tool_calls": [
                        {"id": tc.id, "type": "function",
                         "function": {"name": tc.name,
                                      "arguments": tc.arguments if isinstance(tc.arguments, str)
                                      else json.dumps(tc.arguments)}}
                        for tc in m.tool_calls
                    ],
                })
            else:
                out.append({"role": m.role, "content": text})
        return out

    def _to_litellm_tools(self, request: CanonicalRequest) -> list[dict[str, Any]] | None:
        if not request.tools:
            return None
        return [{"type": "function", "function": {
            "name": t.name, "description": t.description,
            "parameters": t.parameters or {"type": "object"}}}
            for t in request.tools]

    async def stream(self, request: CanonicalRequest) -> AsyncIterator[ModelEvent]:
        import litellm  # already checked

        messages = self._to_litellm_messages(request)
        tools = self._to_litellm_tools(request)
        kwargs: dict[str, Any] = {
            "model": request.model or self.model,
            "messages": messages,
            "stream": True,
        }
        if tools:
            kwargs["tools"] = tools
            kwargs["tool_choice"] = request.tool_choice
        if request.max_tokens is not None:
            kwargs["max_tokens"] = request.max_tokens
        if request.temperature is not None:
            kwargs["temperature"] = request.temperature
        kwargs.update(self.litellm_params)
        kwargs.update(request.provider_options)

        self.attempts += 1
        try:
            resp = await litellm.acompletion(**kwargs)
        except Exception as exc:
            from .http import classify_http_error
            raise classify_http_error(self.provider_name, exc, attempts=1) from None

        tool_slots: dict[int, dict[str, Any]] = {}
        async for chunk in resp:
            # litellm chunks mirror OpenAI shape
            choices = getattr(chunk, "choices", None) or chunk.get("choices", [])
            for choice in choices:
                delta = getattr(choice, "delta", None) or choice.get("delta", {})
                if isinstance(delta, dict):
                    text = delta.get("content")
                    if text:
                        yield ModelEvent(kind="text.delta", text=text, model=request.model)
                    for tc in delta.get("tool_calls", []):
                        idx = int(tc.get("index", 0))
                        slot = tool_slots.setdefault(idx, {"id": "", "name": "", "args": ""})
                        if tc.get("id"):
                            slot["id"] = tc["id"]
                        fn = tc.get("function", {})
                        if fn.get("name"):
                            slot["name"] = fn["name"]
                            yield ModelEvent(kind="tool.begin", tool_id=slot["id"],
                                             tool_name=slot["name"], model=request.model)
                        if fn.get("arguments"):
                            slot["args"] += fn["arguments"]
                            yield ModelEvent(kind="tool.args_delta", tool_id=slot["id"],
                                             tool_name=slot["name"],
                                             tool_args_delta=fn["arguments"], model=request.model)
                else:
                    text = getattr(delta, "content", None)
                    if text:
                        yield ModelEvent(kind="text.delta", text=text, model=request.model)
            # Usage may be in chunk.usage
            usage = getattr(chunk, "usage", None) or chunk.get("usage")
            if usage and isinstance(usage, dict):
                yield ModelEvent(kind="usage", model=request.model,
                                 input_tokens=int(usage.get("prompt_tokens", 0)),
                                 output_tokens=int(usage.get("completion_tokens", 0)))
        for idx in sorted(tool_slots):
            slot = tool_slots[idx]
            try:
                parsed = json.loads(slot["args"]) if slot["args"] else {}
            except json.JSONDecodeError as exc:
                yield ModelEvent(kind="error", model=request.model,
                                 tool_id=slot["id"], tool_name=slot["name"],
                                 error=f"malformed tool arguments: {exc}")
                continue
            yield ModelEvent(kind="tool.done", model=request.model,
                             tool_id=slot["id"], tool_name=slot["name"],
                             tool_arguments=parsed)
        yield ModelEvent(kind="done", model=request.model, stop_reason="stop")
