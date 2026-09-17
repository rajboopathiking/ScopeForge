"""Canonical model-provider types (Phase 3).

Provider-agnostic shapes per plan.md §8.1. Deliberately dataclasses + stdlib:
hot-path events stay allocation-light and importable without third-party deps.
Pydantic enters in Phase 4 for persisted domain records.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, AsyncIterator, Literal, Protocol

Role = Literal["system", "user", "assistant", "tool"]
ADAPTER_VERSION = "0.1.0"


@dataclass(frozen=True)
class TextBlock:
    text: str


@dataclass(frozen=True)
class ImageBlock:
    """Image/file input. `url` may be https:, data:, or a vault/artifact ref —
    never an inline secret (refs only)."""

    url: str
    detail: str = "auto"


ContentBlock = TextBlock | ImageBlock


@dataclass(frozen=True)
class ToolCallRequest:
    id: str
    name: str
    arguments: str | dict[str, Any]  # raw JSON string until parsed at tool.done


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str = ""
    parameters: dict[str, Any] = field(default_factory=dict)  # JSON Schema


@dataclass(frozen=True)
class Message:
    role: Role
    content: list[ContentBlock] = field(default_factory=list)
    tool_calls: list[ToolCallRequest] | None = None
    tool_call_id: str | None = None  # set on role="tool" results
    name: str | None = None


@dataclass(frozen=True)
class ResponseFormat:
    kind: Literal["text", "json", "json_schema"] = "text"
    schema: dict[str, Any] | None = None
    strict: bool = False


@dataclass(frozen=True)
class ReasoningOptions:
    effort: str | None = None  # low|medium|high (provider-mapped)
    summary: bool = False


@dataclass(frozen=True)
class CanonicalRequest:
    model: str
    messages: list[Message]
    tools: list[ToolSpec] = field(default_factory=list)
    tool_choice: str | dict[str, Any] = "auto"
    response_format: ResponseFormat = field(default_factory=ResponseFormat)
    max_tokens: int | None = None
    temperature: float | None = None
    reasoning: ReasoningOptions | None = None
    provider_options: dict[str, Any] = field(default_factory=dict)  # namespaced extras
    task_class: str = "plan"  # plan|extraction|code|vision|tool_use|validation|report


EventKind = Literal[
    "text.delta",
    "reasoning.delta",
    "tool.begin",
    "tool.args_delta",
    "tool.done",
    "usage",
    "done",
    "error",
]


@dataclass(frozen=True)
class ModelEvent:
    kind: EventKind
    text: str = ""
    reasoning: str = ""
    tool_id: str = ""
    tool_name: str = ""
    tool_args_delta: str = ""
    tool_arguments: Any = None  # parsed JSON at tool.done (dict) or raw str
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float | None = None
    stop_reason: str = ""
    model: str = ""
    error: str = ""
    # How strict-JSON was achieved when the endpoint lacks native support.
    structured_via: str = ""  # "" | "native" | "forced-tool" (recorded, never silent)


@dataclass(frozen=True)
class ModelDescriptor:
    id: str
    provider: str
    context_limit: int | None = None
    note: str = ""


@dataclass(frozen=True)
class ModelCapabilities:
    provider: str
    model: str
    adapter: str
    adapter_version: str = ADAPTER_VERSION
    endpoint_class: str = ""  # openai-chat | anthropic-messages | local-mock
    transport: Literal["hosted", "local"] = "hosted"
    streaming: bool = True
    tool_calling: bool = False
    parallel_tools: bool = False
    strict_structured_output: bool = False
    structured_via_tool: bool = False
    vision: bool = False
    reasoning: bool = False
    prompt_caching: bool = False
    model_listing: bool = False
    max_context: int | None = None
    max_output: int | None = None
    price_in_per_1k: float | None = None
    price_out_per_1k: float | None = None
    verified_live: bool = False  # True only after an authenticated probe


@dataclass(frozen=True)
class TokenEstimate:
    input_tokens: int
    output_tokens: int = 0
    exact: bool = False


def estimate_tokens(request: CanonicalRequest) -> TokenEstimate:
    """Heuristic (~chars/4) until provider usage arrives. Never billed on this."""

    def block_chars(b: ContentBlock) -> int:
        return len(b.text) if isinstance(b, TextBlock) else 256

    total = sum(len(m.role) + sum(block_chars(b) for b in m.content) for m in request.messages)
    total += sum(len(t.name) + len(str(t.parameters)) for t in request.tools)
    return TokenEstimate(input_tokens=max(1, total // 4))


class ModelProvider(Protocol):
    async def list_models(self) -> list[ModelDescriptor]: ...
    async def probe(self, model: str) -> ModelCapabilities: ...
    def stream(self, request: CanonicalRequest) -> AsyncIterator[ModelEvent]: ...
    async def count_tokens(self, request: CanonicalRequest) -> TokenEstimate: ...
    async def close(self) -> None: ...
