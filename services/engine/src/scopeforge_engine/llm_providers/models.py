"""Data models for custom LLM provider configuration."""
from __future__ import annotations

from enum import Enum
from typing import Any
from pydantic import BaseModel, ConfigDict, Field


class ProviderType(str, Enum):
    OPENAI = "openai"
    ANTHROPIC = "anthropic"
    OLLAMA = "ollama"
    OPENROUTER = "openrouter"
    GEMINI = "gemini"
    GROQ = "groq"
    CUSTOM = "custom"
    MOCK = "mock"


class LLMConfig(BaseModel):
    """Configuration for a specific LLM provider and model."""
    model_config = ConfigDict(frozen=False)

    name: str = "default"
    provider: ProviderType = ProviderType.MOCK
    model: str = "claude-3-7-sonnet"
    api_key: str | None = None
    api_base: str | None = None
    temperature: float = 0.1
    max_tokens: int = 4096
    streaming: bool = True
    extra_headers: dict[str, str] = Field(default_factory=dict)
    extra_params: dict[str, Any] = Field(default_factory=dict)


DEFAULT_PROVIDERS: dict[str, LLMConfig] = {
    "claude-3-7-sonnet": LLMConfig(
        name="claude-3-7-sonnet",
        provider=ProviderType.ANTHROPIC,
        model="claude-3-7-sonnet-20250219",
        temperature=0.1,
    ),
    "claude-3-5-sonnet": LLMConfig(
        name="claude-3-5-sonnet",
        provider=ProviderType.ANTHROPIC,
        model="claude-3-5-sonnet-20241022",
        temperature=0.1,
    ),
    "gpt-4o": LLMConfig(
        name="gpt-4o",
        provider=ProviderType.OPENAI,
        model="gpt-4o",
        temperature=0.1,
    ),
    "gpt-4o-mini": LLMConfig(
        name="gpt-4o-mini",
        provider=ProviderType.OPENAI,
        model="gpt-4o-mini",
        temperature=0.1,
    ),
    "ollama-llama3": LLMConfig(
        name="ollama-llama3",
        provider=ProviderType.OLLAMA,
        model="llama3:latest",
        api_base="http://localhost:11434",
        temperature=0.2,
    ),
    "ollama-deepseek-r1": LLMConfig(
        name="ollama-deepseek-r1",
        provider=ProviderType.OLLAMA,
        model="deepseek-r1:latest",
        api_base="http://localhost:11434",
        temperature=0.2,
    ),
    "openrouter-free": LLMConfig(
        name="openrouter-free",
        provider=ProviderType.OPENROUTER,
        model="openrouter/free",
        api_base="https://openrouter.ai/api/v1",
        temperature=0.2,
        extra_headers={
            "HTTP-Referer": "https://github.com/rajboopathiking/ScopeForge",
            "X-Title": "ScopeForge Agent Harness",
        },
    ),
    "openrouter-free-deepseek": LLMConfig(
        name="openrouter-free-deepseek",
        provider=ProviderType.OPENROUTER,
        model="deepseek/deepseek-r1:free",
        api_base="https://openrouter.ai/api/v1",
        temperature=0.2,
        extra_headers={
            "HTTP-Referer": "https://github.com/rajboopathiking/ScopeForge",
            "X-Title": "ScopeForge Agent Harness",
        },
    ),
    "openrouter-free-llama": LLMConfig(
        name="openrouter-free-llama",
        provider=ProviderType.OPENROUTER,
        model="meta-llama/llama-3.3-70b-instruct:free",
        api_base="https://openrouter.ai/api/v1",
        temperature=0.1,
        extra_headers={
            "HTTP-Referer": "https://github.com/rajboopathiking/ScopeForge",
            "X-Title": "ScopeForge Agent Harness",
        },
    ),
    "openrouter-free-gemini": LLMConfig(
        name="openrouter-free-gemini",
        provider=ProviderType.OPENROUTER,
        model="google/gemini-2.0-flash-exp:free",
        api_base="https://openrouter.ai/api/v1",
        temperature=0.1,
        extra_headers={
            "HTTP-Referer": "https://github.com/rajboopathiking/ScopeForge",
            "X-Title": "ScopeForge Agent Harness",
        },
    ),
    "openrouter-claude": LLMConfig(
        name="openrouter-claude",
        provider=ProviderType.OPENROUTER,
        model="anthropic/claude-3.5-sonnet",
        api_base="https://openrouter.ai/api/v1",
        temperature=0.1,
    ),
    "openrouter-deepseek-r1": LLMConfig(
        name="openrouter-deepseek-r1",
        provider=ProviderType.OPENROUTER,
        model="deepseek/deepseek-r1",
        api_base="https://openrouter.ai/api/v1",
        temperature=0.2,
    ),
    "openrouter-deepseek-v3": LLMConfig(
        name="openrouter-deepseek-v3",
        provider=ProviderType.OPENROUTER,
        model="deepseek/deepseek-chat",
        api_base="https://openrouter.ai/api/v1",
        temperature=0.2,
    ),
    "openrouter-llama3": LLMConfig(
        name="openrouter-llama3",
        provider=ProviderType.OPENROUTER,
        model="meta-llama/llama-3.3-70b-instruct",
        api_base="https://openrouter.ai/api/v1",
        temperature=0.1,
    ),
    "openrouter-qwen": LLMConfig(
        name="openrouter-qwen",
        provider=ProviderType.OPENROUTER,
        model="qwen/qwen-2.5-coder-32b-instruct",
        api_base="https://openrouter.ai/api/v1",
        temperature=0.1,
    ),
    "groq-llama3": LLMConfig(
        name="groq-llama3",
        provider=ProviderType.GROQ,
        model="llama-3.3-70b-versatile",
        api_base="https://api.groq.com/openai/v1",
        temperature=0.1,
    ),
    "mock-secops": LLMConfig(
        name="mock-secops",
        provider=ProviderType.MOCK,
        model="mock-security-evaluator",
        temperature=0.0,
    ),
}
