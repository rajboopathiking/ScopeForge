"""Provider presets (Phase 8, Tier 3).

Each preset maps to an OpenAICompatAdapter with correct defaults:
  - vLLM / LM Studio  -> local OpenAI-compatible
  - OpenRouter        -> hosted OpenAI-compatible with proper base_url
  - Azure OpenAI      -> OpenAI-compatible with api-key header + api-version query
  - Bedrock / Vertex  -> flagged as requiring native adapters (not shimmed)

Presets are declarative; runtime still capability-probed.
"""
from __future__ import annotations

from typing import Any

PRESETS: dict[str, dict[str, Any]] = {
    "vllm": {
        "type": "openai-compat",
        "default_base_url": "http://localhost:8000/v1",
        "transport": "local",
        "note": "vLLM OpenAI-compatible server (https://docs.vllm.ai/en/latest/serving/openai_compatible_server/)",
        "models_note": "depends on --model flag",
    },
    "lmstudio": {
        "type": "openai-compat",
        "default_base_url": "http://localhost:1234/v1",
        "transport": "local",
        "note": "LM Studio local server",
    },
    "openrouter": {
        "type": "openai-compat",
        "default_base_url": "https://openrouter.ai/api/v1",
        "transport": "hosted",
        "note": "OpenRouter unified API",
        "auth_header": "Authorization",
        "auth_scheme": "Bearer",
    },
    "azure-openai": {
        "type": "openai-compat",
        "default_base_url": "https://{endpoint}.openai.azure.com/openai/deployments/{deployment}",
        "transport": "hosted",
        "note": "Azure OpenAI — set base_url to your deployment URL; add api-version via extra_query",
        "auth_header": "api-key",
        "auth_scheme": "",
        "default_query": {"api-version": "2024-02-15-preview"},
    },
    "bedrock": {
        "type": "bedrock",
        "transport": "hosted",
        "note": "AWS Bedrock — requires native SigV4 adapter (Phase 8+); preset documents the gap",
        "available": False,
    },
    "vertex": {
        "type": "vertex",
        "transport": "hosted",
        "note": "GCP Vertex AI — requires native adapter; preset documents the gap",
        "available": False,
    },
}


def preset_names() -> list[str]:
    return sorted(PRESETS.keys())


def get_preset(name: str) -> dict[str, Any] | None:
    return PRESETS.get(name)


def build_preset_adapter(preset: str, *, base_url: str = "", credential=None, transport=None):
    """Instantiate adapter for a preset. Raises CapabilityUnsupported if not shimmed."""
    from .errors import CapabilityUnsupported

    cfg = PRESETS.get(preset)
    if cfg is None:
        raise CapabilityUnsupported(f"unknown preset {preset!r}")
    if cfg.get("available") is False:
        raise CapabilityUnsupported(
            f"preset {preset!r} requires a native adapter not yet in this build: {cfg['note']}")
    ptype = cfg["type"]
    url = base_url or cfg.get("default_base_url", "")
    if not url:
        raise CapabilityUnsupported(f"preset {preset!r} needs base_url")
    from .openai_compat import OpenAICompatAdapter

    kwargs: dict[str, Any] = {}
    if cfg.get("auth_header"):
        kwargs["auth_header"] = cfg["auth_header"]
    if cfg.get("auth_scheme") is not None:
        kwargs["auth_scheme"] = cfg["auth_scheme"]
    if cfg.get("default_query"):
        kwargs["extra_query"] = {**cfg["default_query"], **({} if not base_url else {})}
    return OpenAICompatAdapter(
        base_url=url, credential=credential,
        provider_name=preset, transport=cfg.get("transport", "hosted"),
        transport_override=transport, **kwargs)
