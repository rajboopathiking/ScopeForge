"""ScopeForge model-provider layer (Phase 3, plan.md §8).

Tier 1 now: generic OpenAI-compatible (+ DeepSeek preset), generic
Anthropic-compatible, local mock. Native Tier-2 adapters land in beta.
"""
from .errors import CapabilityUnsupported, CredentialMissing, ProviderError
from .mock import MockAdapter
from .router import RoutingDecision, route
from .sanitize import assert_no_secrets, sanitize_headers, sanitize_json
from .secrets import CredentialRef, parse_credential_reference, resolve
from .types import (
    ADAPTER_VERSION,
    CanonicalRequest,
    ContentBlock,
    ImageBlock,
    Message,
    ModelCapabilities,
    ModelDescriptor,
    ModelEvent,
    ModelProvider,
    ReasoningOptions,
    ResponseFormat,
    TextBlock,
    TokenEstimate,
    ToolCallRequest,
    ToolSpec,
    estimate_tokens,
)

# HTTP-backed adapters need httpx/anyio (uv env). On bare interpreters the
# package still imports for the stdlib-only paths (sanitize, router, mock);
# server.py maps the missing runtime to JSON-RPC 1003. See docs/development.md.
try:
    from .anthropic_compat import AnthropicCompatAdapter
    from .gemini import GeminiAdapter
    from .ollama import OllamaAdapter
    from .openai_compat import DeepSeekAdapter, OpenAICompatAdapter
    from .presets import PRESETS, build_preset_adapter, get_preset, preset_names
    from .replay import ReplayTransport, load_exchanges

    try:
        from .litellm_bridge import LiteLLMBridgeAdapter
    except ImportError:
        LiteLLMBridgeAdapter = None  # type: ignore

    HTTP_AVAILABLE = True
except ImportError:
    HTTP_AVAILABLE = False

__all__ = [
    "ADAPTER_VERSION", "AnthropicCompatAdapter", "CanonicalRequest", "CapabilityUnsupported",
    "ContentBlock", "CredentialMissing", "CredentialRef", "DeepSeekAdapter",
    "GeminiAdapter", "HTTP_AVAILABLE", "ImageBlock", "LiteLLMBridgeAdapter",
    "Message", "MockAdapter", "ModelCapabilities", "ModelDescriptor", "ModelEvent",
    "ModelProvider", "OllamaAdapter", "OpenAICompatAdapter", "PRESETS",
    "ProviderError", "ReasoningOptions", "ReplayTransport", "ResponseFormat",
    "RoutingDecision", "TextBlock", "TokenEstimate", "ToolCallRequest", "ToolSpec",
    "assert_no_secrets", "build_preset_adapter", "estimate_tokens", "get_preset",
    "load_exchanges", "parse_credential_reference", "preset_names", "resolve",
    "route", "sanitize_headers", "sanitize_json",
]
