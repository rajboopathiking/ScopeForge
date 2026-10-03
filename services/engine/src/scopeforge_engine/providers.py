"""Configured + declared provider registry (shared by server and agent).

Stdlib only. No network, no secrets: capability facts safe to display.
"""
from __future__ import annotations

from pathlib import Path

# Declared provider registry (stdlib mirror of models/ adapter defaults).
# No network, no secrets: values here are capability facts, safe to display.
# The live HTTP path (httpx) is lazily imported only for model.probe live=true.
PROVIDER_TYPES: dict[str, dict] = {
    "mock": {
        "adapter": "mock", "endpoint_class": "local-mock", "transport": "local",
        "models": ["mock-turn"],
        "capabilities": {
            "streaming": True, "tool_calling": True, "parallel_tools": True,
            "strict_structured_output": True, "structured_via_tool": False,
            "vision": True, "reasoning": True, "prompt_caching": False,
            "model_listing": True, "max_context": 128000,
        },
    },
    "openai-compat": {
        "adapter": "openai-compat", "endpoint_class": "openai-chat", "transport": "hosted",
        "models": [], "models_note": "no universal default; set model explicitly",
        "capabilities": {
            "streaming": True, "tool_calling": True, "parallel_tools": True,
            "strict_structured_output": True, "structured_via_tool": False,
            "vision": True, "reasoning": False, "prompt_caching": False,
            "model_listing": True, "max_context": None,
        },
    },
    "deepseek": {
        "adapter": "deepseek", "endpoint_class": "openai-chat", "transport": "hosted",
        "default_base_url": "https://api.deepseek.com",
        "models": ["deepseek-chat", "deepseek-reasoner"],
        "model_limits": {"deepseek-reasoner": {"tool_calling": False}},
        "capabilities": {
            "streaming": True, "tool_calling": True, "parallel_tools": True,
            "strict_structured_output": True, "structured_via_tool": False,
            "vision": True, "reasoning": True, "prompt_caching": False,
            "model_listing": True, "max_context": 64000,
        },
    },
    "anthropic-compat": {
        "adapter": "anthropic-compat", "endpoint_class": "anthropic-messages",
        "transport": "hosted",
        "models": [], "models_note": "no list endpoint; set model explicitly",
        "capabilities": {
            "streaming": True, "tool_calling": True, "parallel_tools": True,
            "strict_structured_output": False, "structured_via_tool": True,
            "vision": True, "reasoning": False, "prompt_caching": False,
            "model_listing": False, "max_context": 200000,
        },
    },
    "gemini": {
        "adapter": "gemini", "endpoint_class": "google-gemini", "transport": "hosted",
        "default_base_url": "https://generativelanguage.googleapis.com",
        "models": ["gemini-2.0-flash", "gemini-1.5-pro"],
        "capabilities": {
            "streaming": True, "tool_calling": True, "parallel_tools": True,
            "strict_structured_output": False, "structured_via_tool": True,
            "vision": True, "reasoning": False, "prompt_caching": True,
            "model_listing": True, "max_context": 1000000,
        },
    },
    "ollama": {
        "adapter": "ollama", "endpoint_class": "ollama-chat", "transport": "local",
        "default_base_url": "http://localhost:11434",
        "models": [], "models_note": "local models via /api/tags",
        "capabilities": {
            "streaming": True, "tool_calling": True, "parallel_tools": False,
            "strict_structured_output": False, "structured_via_tool": False,
            "vision": False, "reasoning": False, "prompt_caching": False,
            "model_listing": True, "max_context": 8192,
        },
    },
    "vllm": {
        "adapter": "openai-compat", "endpoint_class": "openai-chat", "transport": "local",
        "default_base_url": "http://localhost:8000/v1",
        "capabilities": {
            "streaming": True, "tool_calling": True, "parallel_tools": True,
            "strict_structured_output": True, "structured_via_tool": False,
            "vision": False, "reasoning": False, "prompt_caching": False,
            "model_listing": True, "max_context": None,
        },
    },
    "lmstudio": {
        "adapter": "openai-compat", "endpoint_class": "openai-chat", "transport": "local",
        "default_base_url": "http://localhost:1234/v1",
        "capabilities": {
            "streaming": True, "tool_calling": True, "parallel_tools": True,
            "strict_structured_output": True, "structured_via_tool": False,
            "vision": False, "reasoning": False, "prompt_caching": False,
            "model_listing": True, "max_context": None,
        },
    },
    "openrouter": {
        "adapter": "openai-compat", "endpoint_class": "openai-chat", "transport": "hosted",
        "default_base_url": "https://openrouter.ai/api/v1",
        "capabilities": {
            "streaming": True, "tool_calling": True, "parallel_tools": True,
            "strict_structured_output": True, "structured_via_tool": False,
            "vision": True, "reasoning": False, "prompt_caching": False,
            "model_listing": True, "max_context": None,
        },
    },
    "azure-openai": {
        "adapter": "openai-compat", "endpoint_class": "openai-chat", "transport": "hosted",
        "capabilities": {
            "streaming": True, "tool_calling": True, "parallel_tools": True,
            "strict_structured_output": True, "structured_via_tool": False,
            "vision": True, "reasoning": False, "prompt_caching": False,
            "model_listing": True, "max_context": None,
        },
    },
    "litellm": {
        "adapter": "litellm-bridge", "endpoint_class": "litellm", "transport": "hosted",
        "models": ["openai/gpt-4o-mini"],
        "capabilities": {
            "streaming": True, "tool_calling": True, "parallel_tools": True,
            "strict_structured_output": False, "structured_via_tool": True,
            "vision": False, "reasoning": False, "prompt_caching": False,
            "model_listing": False, "max_context": None,
        },
    },
}


def load_provider_config(store_root: Path) -> dict[str, dict]:
    """Read providers.toml (secret REFERENCES only, never values)."""
    import tomllib

    path = Path(store_root) / "providers.toml"
    try:
        text = path.read_text()
    except OSError:
        return {}
    try:
        data = tomllib.loads(text)
    except (tomllib.TOMLDecodeError, ValueError):
        return {}
    out: dict[str, dict] = {}
    providers = data.get("providers", {})
    if isinstance(providers, dict):
        for name, cfg in providers.items():
            if isinstance(cfg, dict) and cfg.get("type") in PROVIDER_TYPES:
                out[str(name)] = {
                    "type": str(cfg["type"]),
                    "base_url": str(cfg.get("base_url", "")),
                    "credential": str(cfg.get("credential", "")),
                }
    return out

