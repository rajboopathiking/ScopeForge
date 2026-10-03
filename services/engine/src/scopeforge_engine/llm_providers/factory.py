"""Factory for creating LangChain Chat Models based on provider configuration."""
from __future__ import annotations

import os
from typing import Any
from langchain_core.language_models.chat_models import BaseChatModel

from .models import LLMConfig, ProviderType
from .mock import MockSecOpsChatModel


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
            headers = dict(config.extra_headers)
            if provider == ProviderType.OPENROUTER:
                api_key = api_key or os.getenv("OPENROUTER_API_KEY")
                base_url = base_url or "https://openrouter.ai/api/v1"
                headers.setdefault("HTTP-Referer", "https://github.com/rajboopathiking/ScopeForge")
                headers.setdefault("X-Title", "ScopeForge Agent Harness")

            if not api_key and provider == ProviderType.OPENAI:
                return MockSecOpsChatModel(model_name=f"[MOCK fallback: missing OPENAI_API_KEY] {config.model}")

            return ChatOpenAI(
                model=config.model,
                api_key=api_key or "sk-dummy",
                base_url=base_url,
                temperature=config.temperature,
                max_tokens=config.max_tokens,
                streaming=config.streaming,
                default_headers=headers,
                **config.extra_params,
            )
        except Exception:
            return MockSecOpsChatModel(model_name=f"[MOCK fallback] {config.model}")

    elif provider == ProviderType.OLLAMA:
        try:
            # Use ChatOpenAI with Ollama's OpenAI-compatible endpoint or ChatOllama
            from langchain_openai import ChatOpenAI
            base_url = config.api_base or "http://localhost:11434/v1"
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
