"""Manager for LLM providers, active model switching, and registry persistence."""
from __future__ import annotations

import os
from pathlib import Path
from typing import Dict, List, Optional
import yaml
from langchain_core.language_models.chat_models import BaseChatModel

from .models import DEFAULT_PROVIDERS, LLMConfig, ProviderType
from .factory import create_chat_model


class ProviderManager:
    """Manages available LLM providers, model selection, and active LangChain chat models."""

    def __init__(self, config_dir: Optional[Path] = None):
        self.config_dir = config_dir or Path(".scopeforge")
        self.config_path = self.config_dir / "providers.yaml"
        self.providers: Dict[str, LLMConfig] = dict(DEFAULT_PROVIDERS)
        self.active_provider_name: str = "claude-3-7-sonnet"
        self._cached_chat_model: Optional[BaseChatModel] = None

        self._load_config()
        self._auto_detect_defaults()

    def _auto_detect_defaults(self):
        """Auto-detect API keys from environment and set sensible default."""
        if os.getenv("ANTHROPIC_API_KEY"):
            self.active_provider_name = "claude-3-7-sonnet"
        elif os.getenv("OPENAI_API_KEY"):
            self.active_provider_name = "gpt-4o"
        elif os.getenv("OPENROUTER_API_KEY"):
            self.active_provider_name = "openrouter-free"
        elif os.getenv("GROQ_API_KEY"):
            self.active_provider_name = "groq-llama3"
        else:
            self.active_provider_name = "mock-secops"

    def _load_config(self):
        """Load user custom providers from YAML if present."""
        if self.config_path.exists():
            try:
                with open(self.config_path, "r", encoding="utf-8") as f:
                    data = yaml.safe_load(f) or {}
                active = data.get("active")
                if active and active in self.providers:
                    self.active_provider_name = active
                custom_list = data.get("providers", {})
                for name, p_data in custom_list.items():
                    self.providers[name] = LLMConfig(**p_data)
            except Exception:
                pass

    def save_config(self):
        """Persist provider settings to disk."""
        self.config_dir.mkdir(parents=True, exist_ok=True)
        data = {
            "active": self.active_provider_name,
            "providers": {k: v.model_dump(mode="json") for k, v in self.providers.items()},
        }
        with open(self.config_path, "w", encoding="utf-8") as f:
            yaml.safe_dump(data, f, sort_keys=False)

    def list_providers(self) -> List[LLMConfig]:
        """Return all registered providers."""
        return list(self.providers.values())

    def get_active_config(self) -> LLMConfig:
        """Return active LLMConfig."""
        return self.providers.get(self.active_provider_name, self.providers["mock-secops"])

    def set_active_provider(self, name: str) -> bool:
        """Switch the active provider."""
        if name in self.providers:
            self.active_provider_name = name
            self._cached_chat_model = None
            self.save_config()
            return True
        if name.lower() in ("free", "openrouter/free", "openrouter:free"):
            self.active_provider_name = "openrouter-free"
            self._cached_chat_model = None
            self.save_config()
            return True
        # Try matching by model name substring
        for k, v in self.providers.items():
            if name.lower() in k.lower() or name.lower() in v.model.lower():
                self.active_provider_name = k
                self._cached_chat_model = None
                self.save_config()
                return True
        return False

    def register_provider(self, config: LLMConfig):
        """Add or update a provider config."""
        self.providers[config.name] = config
        self.save_config()

    def get_chat_model(self, force_refresh: bool = False) -> BaseChatModel:
        """Get or instantiate the active LangChain ChatModel."""
        if self._cached_chat_model is None or force_refresh:
            config = self.get_active_config()
            self._cached_chat_model = create_chat_model(config)
        return self._cached_chat_model
