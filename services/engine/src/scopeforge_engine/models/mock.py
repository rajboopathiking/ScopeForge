"""Local mock provider (Phase 3): scripted turns for offline dev, `provider
test`, and the conformance suite's third shape. Transport is local; probe is
always verified (no network exists to fail)."""
from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

from .types import (
    ADAPTER_VERSION,
    CanonicalRequest,
    ModelCapabilities,
    ModelDescriptor,
    ModelEvent,
    TokenEstimate,
    estimate_tokens,
)


class MockAdapter:
    adapter_name = "mock"
    endpoint_class = "local-mock"

    def __init__(
        self,
        *,
        provider_name: str = "mock",
        script: list[list[ModelEvent]] | None = None,
        models: list[str] | None = None,
        capabilities: ModelCapabilities | None = None,
    ) -> None:
        self.provider_name = provider_name
        self.script = script or []
        self.calls = 0
        self.requests: list[CanonicalRequest] = []
        self._models = models or ["mock-turn"]
        self._caps = capabilities

    async def list_models(self) -> list[ModelDescriptor]:
        return [ModelDescriptor(id=m, provider=self.provider_name) for m in self._models]

    async def probe(self, model: str, *, verify_reachability: bool = False) -> ModelCapabilities:
        _ = verify_reachability
        if self._caps is not None:
            return self._caps
        return ModelCapabilities(
            provider=self.provider_name, model=model, adapter=self.adapter_name,
            adapter_version=ADAPTER_VERSION, endpoint_class=self.endpoint_class,
            transport="local", tool_calling=True, parallel_tools=True,
            strict_structured_output=True, vision=True, reasoning=True,
            prompt_caching=False, model_listing=True, max_context=128000,
            verified_live=True,
        )

    async def count_tokens(self, request: CanonicalRequest) -> TokenEstimate:
        est = estimate_tokens(request)
        return TokenEstimate(input_tokens=est.input_tokens, exact=True)

    async def close(self) -> None:
        pass

    async def stream(self, request: CanonicalRequest) -> AsyncIterator[ModelEvent]:
        self.requests.append(request)
        turn = self.script[self.calls] if self.calls < len(self.script) else []
        self.calls += 1
        for event in turn:
            yield event
        if not any(e.kind == "done" for e in turn):
            yield ModelEvent(kind="done", model=request.model, stop_reason="stop")

    def script_turn(self, events: list[ModelEvent]) -> None:
        self.script.append(events)


def turn(events: list[dict[str, Any]], model: str = "mock-turn") -> list[ModelEvent]:
    """Build a scripted turn from compact dicts (tests + fixtures)."""
    out: list[ModelEvent] = []
    for e in events:
        out.append(ModelEvent(kind=e["kind"], text=e.get("text", ""),
                              reasoning=e.get("reasoning", ""),
                              tool_id=e.get("tool_id", ""), tool_name=e.get("tool_name", ""),
                              tool_arguments=e.get("tool_arguments"),
                              input_tokens=e.get("input_tokens", 0),
                              output_tokens=e.get("output_tokens", 0),
                              stop_reason=e.get("stop_reason", ""),
                              model=model, error=e.get("error", ""),
                              structured_via=e.get("structured_via", "")))
    return out
