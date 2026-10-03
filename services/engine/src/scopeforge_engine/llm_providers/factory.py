"""Factory for creating LangChain Chat Models based on provider configuration."""
from __future__ import annotations

import os
from typing import Any
from langchain_core.language_models.chat_models import BaseChatModel

from .models import LLMConfig, ProviderType
from .mock import MockSecOpsChatModel


def _normalize_ollama_base(base: str | None) -> str:
    """Ollama OpenAI-compat endpoint must end with /v1 for ChatOpenAI."""
    b = (base or "http://localhost:11434/v1").strip().rstrip("/")
    if b.endswith("/api"):
        # `http://host:11434/api` -> `http://host:11434/v1`
        b = b[: -len("/api")] + "/v1"
    if not b.endswith("/v1"):
        # `http://host:11434` -> `http://host:11434/v1` (docs vs stored config drift)
        b = b + "/v1"
    return b


def create_chat_model(config: LLMConfig) -> BaseChatModel:
    """Instantiate a LangChain chat model from LLMConfig."""
    provider = config.provider

    if provider == ProviderType.MOCK:
        return MockSecOpsChatModel(model_name=config.model, streaming=config.streaming)

    elif provider == ProviderType.ANTHROPIC:
        try:
            from langchain_anthropic import ChatAnthropic
            api_key = config.api_key or os.getenv("ANTHROPIC_API_KEY")
            if not api_key:
                # Return mock if key is missing
                return MockSecOpsChatModel(model_name=f"[MOCK fallback: missing ANTHROPIC_API_KEY] {config.model}")
            return ChatAnthropic(
                model=config.model,
                api_key=api_key,
                temperature=config.temperature,
                max_tokens=config.max_tokens,
                streaming=config.streaming,
                **config.extra_params,
            )
        except Exception:
            return MockSecOpsChatModel(model_name=f"[MOCK fallback] {config.model}")

    elif provider == ProviderType.GEMINI:
        # Native Gemini via langchain-google-genai when installed,
        # otherwise OpenAI-compat via OpenRouter is handled by caller config.
        try:
            from langchain_google_genai import ChatGoogleGenerativeAI  # type: ignore
            api_key = config.api_key or os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")
            if not api_key:
                return MockSecOpsChatModel(
                    model_name=f"[MOCK fallback: missing GEMINI_API_KEY] {config.model}"
                )
            kwargs: dict[str, Any] = dict(
                model=config.model,
                google_api_key=api_key,
                temperature=config.temperature,
            )
            if config.max_tokens:
                kwargs["max_output_tokens"] = config.max_tokens
            if config.api_base and "generativelanguage" not in config.api_base:
                # Custom proxy endpoint; langchain_google_genai honours client_options
                kwargs["client_options"] = {"api_endpoint": config.api_base}
            return ChatGoogleGenerativeAI(**kwargs)
        except ImportError:
            # Optional dep missing -> clear mock marker so supervisor can
            # surface actionable guidance instead of generic capabilities.
            return MockSecOpsChatModel(
                model_name=f"[MOCK fallback: langchain-google-genai not installed] {config.model}"
            )
        except Exception:
            return MockSecOpsChatModel(model_name=f"[MOCK fallback] {config.model}")

    elif provider in (ProviderType.OPENAI, ProviderType.GROQ, ProviderType.OPENROUTER, ProviderType.CUSTOM):
        try:
            from langchain_openai import ChatOpenAI
            api_key = config.api_key
            base_url = config.api_base

            if provider == ProviderType.OPENAI:
                api_key = api_key or os.getenv("OPENAI_API_KEY")
            elif provider == ProviderType.GROQ:
                api_key = api_key or os.getenv("GROQ_API_KEY")
                base_url = base_url or "https://api.groq.com/openai/v1"
            elif provider == ProviderType.OPENROUTER:
                api_key = api_key or os.getenv("OPENROUTER_API_KEY")
                base_url = base_url or "https://openrouter.ai/api/v1"
            elif provider == ProviderType.CUSTOM:
                # Custom: inherit env when base looks like a known aggregator,
                # otherwise require explicit key (no silent `sk-dummy` against OpenAI).
                if base_url and "openrouter" in base_url:
                    api_key = api_key or os.getenv("OPENROUTER_API_KEY")
                elif base_url and "groq" in base_url:
                    api_key = api_key or os.getenv("GROQ_API_KEY")
                elif not base_url and not api_key:
                    api_key = os.getenv("OPENAI_API_KEY")
            headers = dict(config.extra_headers)
            if provider == ProviderType.OPENROUTER or (base_url and "openrouter" in base_url):
                api_key = api_key or os.getenv("OPENROUTER_API_KEY")
                base_url = base_url or "https://openrouter.ai/api/v1"
                headers.setdefault("HTTP-Referer", "https://github.com/rajboopathiking/ScopeForge")
                headers.setdefault("X-Title", "ScopeForge Agent Harness")

            if not api_key and provider == ProviderType.OPENAI:
                return MockSecOpsChatModel(model_name=f"[MOCK fallback: missing OPENAI_API_KEY] {config.model}")
            if not api_key and provider == ProviderType.OPENROUTER:
                return MockSecOpsChatModel(
                    model_name=f"[MOCK fallback: missing OPENROUTER_API_KEY] {config.model}"
                )
            if provider == ProviderType.CUSTOM and not api_key and not base_url:
                return MockSecOpsChatModel(
                    model_name=f"[MOCK fallback: custom model missing key+base_url] {config.model}"
                )

            return ChatOpenAI(
                model=config.model,
                api_key=api_key or "sk-dummy",
                base_url=base_url,
                temperature=config.temperature,
                max_tokens=config.max_tokens,
                streaming=config.streaming,
                default_headers=headers or None,
                **config.extra_params,
            )
        except Exception:
            return MockSecOpsChatModel(model_name=f"[MOCK fallback] {config.model}")

    elif provider == ProviderType.OLLAMA:
        try:
            # Use ChatOpenAI with Ollama's OpenAI-compatible endpoint or ChatOllama
            from langchain_openai import ChatOpenAI
            base_url = _normalize_ollama_base(config.api_base)
            return ChatOpenAI(
                model=config.model,
                api_key="ollama",
                base_url=base_url,
                temperature=config.temperature,
                streaming=config.streaming,
            )
        except Exception:
            return MockSecOpsChatModel(model_name=f"[MOCK fallback: Ollama unavailable] {config.model}")

    # Default fallback
    return MockSecOpsChatModel(model_name=config.model)
