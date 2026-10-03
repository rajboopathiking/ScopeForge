"""Adapter factory: provider type + config -> adapter instance.

Single construction site shared by live probes, the agent loop, and (Phase 7)
the executor — capability checks stay identical everywhere.
"""
from __future__ import annotations

from typing import Any

from .errors import CapabilityUnsupported


def build_adapter(provider_type: str, *, base_url: str = "",
                  credential=None, transport=None):
    """Build an adapter. Raises CapabilityUnsupported for unknown types."""
    # Local imports keep bare-interpreter imports of this module cheap.
    if provider_type == "mock":
        from .mock import MockAdapter

        return MockAdapter()
    if provider_type == "openai-compat":
        from .openai_compat import OpenAICompatAdapter

        if not base_url:
            raise CapabilityUnsupported("openai-compat needs a base_url")
        return OpenAICompatAdapter(base_url=base_url, credential=credential,
                                   transport_override=transport)
    if provider_type == "deepseek":
        from .openai_compat import DeepSeekAdapter

        kw: dict[str, Any] = {"credential": credential}
        if base_url:
            kw["base_url"] = base_url
        if transport is not None:
            kw["transport_override"] = transport
        return DeepSeekAdapter(**kw)
    if provider_type == "anthropic-compat":
        from .anthropic_compat import AnthropicCompatAdapter

        if not base_url:
            raise CapabilityUnsupported("anthropic-compat needs a base_url")
        return AnthropicCompatAdapter(base_url=base_url, credential=credential,
                                      transport_override=transport)
    if provider_type == "gemini":
        from .gemini import GeminiAdapter

        return GeminiAdapter(credential=credential, base_url=base_url or "https://generativelanguage.googleapis.com",
                             transport_override=transport)
    if provider_type == "ollama":
        from .ollama import OllamaAdapter

        return OllamaAdapter(base_url=base_url or "http://localhost:11434",
                             credential=credential, transport_override=transport)
    if provider_type in ("vllm", "lmstudio", "openrouter", "azure-openai"):
        from .presets import build_preset_adapter

        return build_preset_adapter(provider_type, base_url=base_url,
                                    credential=credential, transport=transport)
    if provider_type == "litellm":
        from .litellm_bridge import LiteLLMBridgeAdapter

        return LiteLLMBridgeAdapter(model=base_url or "openai/gpt-4o-mini")
    raise CapabilityUnsupported(f"unknown provider type {provider_type!r}")
