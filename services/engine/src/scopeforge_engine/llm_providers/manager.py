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

    @staticmethod
    def _resolve_config_dir(explicit: Optional[Path] = None) -> Path:
        # Single source of truth for `.scopeforge/providers.yaml`.
        # Previously `Path(".scopeforge")` was cwd-dependent, so repo-root TUI
        # and `services/engine` tests used DIFFERENT files — curl worked but
        # TUI read a stale ollama/mock config (Claude Code never splits like this).
        if explicit is not None:
            return explicit
        env_home = os.getenv("SCOPEFORGE_HOME")
        if env_home:
            return Path(env_home)
        cwd = Path.cwd()
        # Walk up looking for an existing `.scopeforge/providers.yaml`
        for parent in [cwd, *cwd.parents]:
            candidate = parent / ".scopeforge" / "providers.yaml"
            if candidate.exists():
                return parent / ".scopeforge"
            # Stop at filesystem root or home to avoid escaping
            if parent == parent.parent:
                break
        return cwd / ".scopeforge"

    def __init__(self, config_dir: Optional[Path] = None):
        self.config_dir = self._resolve_config_dir(config_dir)
        self.config_path = self.config_dir / "providers.yaml"
        self.providers: Dict[str, LLMConfig] = dict(DEFAULT_PROVIDERS)
        self.active_provider_name: str = "claude-3-7-sonnet"
        self._cached_chat_model: Optional[BaseChatModel] = None
        self._has_persisted_active: bool = False
        # Names registered via add_custom_provider / loaded YAML entries that
        # are not stock defaults. Used by the picker so stock models are never
        # mislabeled [CUSTOM].
        self.custom_provider_names: set[str] = set()

        self._load_config()
        # Only auto-detect when there is no persisted user choice.
        # Previously this unconditionally overwrote the saved `active`,
        # so custom `/model add` selections were lost on restart.
        if not self._has_persisted_active:
            self._auto_detect_defaults()

    def _auto_detect_defaults(self):
        """Auto-detect API keys from environment and set sensible default."""
        if os.getenv("ANTHROPIC_API_KEY"):
            self.active_provider_name = "claude-3-7-sonnet"
        elif os.getenv("OPENAI_API_KEY"):
            self.active_provider_name = "gpt-4o"
        elif os.getenv("OPENROUTER_API_KEY"):
            # `openrouter/free` is the documented OpenRouter slug (OpenAI-compatible,
            # https://openrouter.ai/api/v1/chat/completions) — verified live with curl.
            self.active_provider_name = "openrouter-free"
        elif os.getenv("GROQ_API_KEY"):
            self.active_provider_name = "groq-llama3"
        elif os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY"):
            # Prefer native gemini preset when available, else openrouter gemini free
            if "gemini-flash" in self.providers:
                self.active_provider_name = "gemini-flash"
            elif "openrouter-free-gemini" in self.providers:
                self.active_provider_name = "openrouter-free-gemini"
            else:
                self.active_provider_name = "mock-secops"
        else:
            self.active_provider_name = "mock-secops"

    @staticmethod
    def _sanitize_api_key(raw_key: Optional[str]) -> Optional[str]:
        """Strip quotes, backslashes, bearer prefix, deduplicate double-pastes, or extract key from pasted curl snippets."""
        if not raw_key:
            return None
        text = str(raw_key).strip().strip("'\"\\ \t\r\n")
        if not text:
            return None

        import re

        # Extract Authorization: Bearer <key> from pasted curl commands or headers
        bearer_m = re.search(r"Bearer\s+([A-Za-z0-9_.-]+)", text, re.IGNORECASE)
        if bearer_m:
            text = bearer_m.group(1).strip()

        # Deduplicate split pastes e.g. "key key" or "key,key" or "key;key"
        parts = [p.strip() for p in re.split(r"[\s,;]+", text) if p.strip()]
        if len(parts) >= 2 and all(p == parts[0] for p in parts):
            text = parts[0]

        # Deduplicate exact halves concatenated e.g. "keykey"
        if len(text) >= 16 and len(text) % 2 == 0:
            half = len(text) // 2
            if text[:half] == text[half:]:
                text = text[:half]

        # Deduplicate repeated prefix e.g. apx_live_...apx_live_...
        for pfx in ("apx_live_", "apx_", "sk-or-v1-", "sk-ant-", "gsk_", "sk-", "hf_"):
            if text.startswith(pfx):
                second_idx = text.find(pfx, len(pfx))
                if second_idx > 0 and text[:second_idx] == text[second_idx:]:
                    text = text[:second_idx]
                    break

        # Extract known key patterns if wrapped or embedded
        m = re.search(
            r"(apx_live_[A-Za-z0-9_-]+|apx_[A-Za-z0-9_-]+|sk-or-v1-[A-Za-z0-9_-]+|sk-ant-[A-Za-z0-9_-]+|gsk_[A-Za-z0-9_-]+|sk-[A-Za-z0-9_-]+|hf_[A-Za-z0-9_-]+)",
            text,
        )
        if m:
            extracted = m.group(1).strip()
            if len(extracted) >= 16 and len(extracted) % 2 == 0:
                half = len(extracted) // 2
                if extracted[:half] == extracted[half:]:
                    extracted = extracted[:half]
            return extracted

        if text.lower().startswith("bearer "):
            text = text[7:].strip()
        if text.startswith("curl ") or text.startswith("http"):
            return None
        return text or None

    def is_custom_provider(self, name: str) -> bool:
        """True only for user-added models — never for stock defaults."""
        if name in self.custom_provider_names:
            return True
        return name not in DEFAULT_PROVIDERS and name != "mock-secops"

    @staticmethod
    def resolve_key_ref(raw_key: Optional[str]) -> Optional[str]:
        """Resolve `env:VAR` without persisting secrets. Returns None when the
        reference points at a set env var (factory reads env at runtime)."""
        if not raw_key:
            return None
        text = str(raw_key).strip()
        if text.lower().startswith("env:"):
            var = text[4:].strip()
            if not var:
                raise ValueError("API Key 'env:' must name a variable, e.g. env:OPENROUTER_API_KEY")
            if not os.getenv(var):
                raise ValueError(f"env:{var} is not set in this environment")
            return None
        return text or None

    def _load_config(self):
        """Load user custom providers from YAML if present."""
        if self.config_path.exists():
            try:
                with open(self.config_path, "r", encoding="utf-8") as f:
                    data = yaml.safe_load(f) or {}
                custom_list = data.get("providers", {})
                migrated_any = False
                for name, p_data in custom_list.items():
                    try:
                        cfg = LLMConfig(**p_data)
                        old_json = cfg.model_dump(mode="json")
                        cfg = self._migrate_config(cfg)
                        if cfg.model_dump(mode="json") != old_json:
                            migrated_any = True
                        self.providers[name] = cfg
                        if name not in DEFAULT_PROVIDERS and name != "mock-secops":
                            self.custom_provider_names.add(name)
                    except Exception:
                        continue
                active = data.get("active")
                if active and active in self.providers:
                    self.active_provider_name = active
                    self._has_persisted_active = True

                # Propagate discovered valid keys across same provider type & os.environ
                known_keys = {}
                for p in self.providers.values():
                    if p.api_key:
                        clean_k = self._sanitize_api_key(p.api_key)
                        p.api_key = clean_k
                        if clean_k:
                            known_keys.setdefault(p.provider, clean_k)

                for ptype, key in known_keys.items():
                    if ptype == ProviderType.OPENROUTER and not os.getenv("OPENROUTER_API_KEY"):
                        os.environ["OPENROUTER_API_KEY"] = key
                    elif ptype == ProviderType.OPENAI and not os.getenv("OPENAI_API_KEY"):
                        os.environ["OPENAI_API_KEY"] = key
                    elif ptype == ProviderType.ANTHROPIC and not os.getenv("ANTHROPIC_API_KEY"):
                        os.environ["ANTHROPIC_API_KEY"] = key
                    elif ptype == ProviderType.GROQ and not os.getenv("GROQ_API_KEY"):
                        os.environ["GROQ_API_KEY"] = key

                for p in self.providers.values():
                    if not p.api_key and p.provider in known_keys:
                        p.api_key = known_keys[p.provider]
                    elif not p.api_key and p.provider == ProviderType.OPENROUTER and os.getenv("OPENROUTER_API_KEY"):
                        p.api_key = os.getenv("OPENROUTER_API_KEY")

            except Exception:
                pass

    @staticmethod
    def _normalize_base_for_provider(provider: ProviderType, base: Optional[str]) -> Optional[str]:
        if not base:
            return base
        nb = base.strip().rstrip("/")
        # Strip full-endpoint paste to base (SDK appends path)
        for suffix in ("/chat/completions", "/messages", "/responses"):
            if nb.endswith(suffix):
                nb = nb[: -len(suffix)].rstrip("/")
                break

        if provider == ProviderType.OPENROUTER or "openrouter.ai" in nb.lower():
            if nb.endswith("/api"):
                nb = nb + "/v1"
            elif not nb.endswith("/v1"):
                nb = nb + "/api/v1"
        elif provider == ProviderType.OLLAMA:
            if nb.endswith("/api"):
                nb = nb[: -len("/api")] + "/v1"
            if not nb.endswith("/v1"):
                nb = nb + "/v1"
        elif provider == ProviderType.GROQ or "api.groq.com" in nb.lower():
            if nb in ("https://api.groq.com", "http://api.groq.com"):
                nb = nb + "/openai/v1"
            elif nb.endswith("/openai"):
                nb = nb + "/v1"
            elif not nb.endswith("/v1"):
                nb = nb + "/v1"
        elif provider in (ProviderType.CUSTOM, ProviderType.OPENAI):
            from urllib.parse import urlparse
            p = urlparse(nb)
            path = (p.path or "").rstrip("/")
            if not path or path == "/api":
                nb = nb.rstrip("/") + ("/v1" if path != "/api" else "/v1")
            elif not (
                path.endswith("/v1")
                or path.endswith("/v2")
                or path.endswith("/v3")
                or path.endswith("/v1beta")
                or "/v1" in path
            ):
                nb = nb.rstrip("/") + "/v1"
        return nb

    @staticmethod
    def _migrate_config(cfg: LLMConfig) -> LLMConfig:
        """Self-heal stale persisted configs (old base URLs, missing headers, retired slugs, wrong providers)."""
        cfg.api_key = ProviderManager._sanitize_api_key(cfg.api_key)

        # Heal configs where provider is ANTHROPIC but api_base is a third-party OpenAI-compatible proxy (e.g. apmix.ai)
        if cfg.provider == ProviderType.ANTHROPIC and cfg.api_base and "anthropic" not in cfg.api_base.lower():
            cfg.provider = ProviderType.CUSTOM

        cfg.api_base = ProviderManager._normalize_base_for_provider(cfg.provider, cfg.api_base)
        # Heal retired 404 slugs
        retired_slugs = {
            "deepseek/deepseek-r1:free": "openrouter/free",
            "meta-llama/llama-3.3-70b-instruct:free": "openrouter/free",
            "google/gemini-2.0-flash-exp:free": "google/gemma-4-31b-it:free",
        }
        if cfg.model in retired_slugs:
            cfg.model = retired_slugs[cfg.model]

        # OpenRouter presets must carry attribution headers
        if cfg.provider == ProviderType.OPENROUTER or (cfg.api_base and "openrouter" in cfg.api_base):
            cfg.extra_headers.setdefault(
                "HTTP-Referer", "https://github.com/rajboopathiking/ScopeForge"
            )
            cfg.extra_headers.setdefault("X-Title", "ScopeForge Agent Harness")
        return cfg

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
        low = name.strip().lower()
        if low in ("free", "openrouter/free", "openrouter:free", "openrouter/auto"):
            # Canonical free preset (migrated from legacy `openrouter/free`)
            self.active_provider_name = "openrouter-free"
            self._cached_chat_model = None
            self.save_config()
            return True
        # Try matching by model name substring
        for k, v in self.providers.items():
            if low in k.lower() or low in v.model.lower():
                self.active_provider_name = k
                self._cached_chat_model = None
                self.save_config()
                return True
        return False

    def register_provider(self, config: LLMConfig):
        """Add or update a provider config."""
        self.providers[config.name] = config
        self.save_config()

    def add_custom_provider(
        self,
        name: str,
        model: str,
        api_key: Optional[str] = None,
        api_base: Optional[str] = None,
        provider: Optional[str] = None,
        temperature: float = 0.2,
        max_tokens: int = 4096,
        set_active: bool = True,
    ) -> LLMConfig:
        """Register a custom LLM model using name, model ID, API key, and base URL."""
        model = (model or "").strip()
        if not model:
            raise ValueError("model id is required (e.g. openrouter/free, deepseek/deepseek-chat, gpt-4o)")
        # `env:VAR` never persists a secret — factory reads env at runtime.
        if api_key and str(api_key).strip().lower().startswith("env:"):
            api_key = self.resolve_key_ref(api_key)
        # Strip pasted curl/quotes/bearer prefixes before storing.
        api_key = self._sanitize_api_key(api_key)
        # `openrouter/free`, `openrouter:free`, `openrouter/auto` are all valid OpenRouter slugs — keep as-is.
        name = name.strip() if name and name.strip() else f"custom-{model.replace('/', '-').split(':')[0]}"
        ptype = ProviderType.CUSTOM
        if provider:
            try:
                ptype = ProviderType(provider.lower())
                # If provider was set to anthropic, but api_base is a third-party proxy,
                # third-party proxies serve Claude models via OpenAI-compatible API:
                if ptype == ProviderType.ANTHROPIC and api_base and "anthropic" not in api_base.lower():
                    ptype = ProviderType.CUSTOM
            except Exception:
                ptype = ProviderType.CUSTOM
        elif api_base and "openrouter.ai" in api_base:
            ptype = ProviderType.OPENROUTER
        elif api_base and "anthropic" in api_base:
            ptype = ProviderType.ANTHROPIC
        elif api_base and "groq" in api_base:
            ptype = ProviderType.GROQ
        elif api_base and ("localhost:11434" in api_base or "ollama" in api_base.lower()):
            ptype = ProviderType.OLLAMA
        elif api_base and ("localhost:8000" in api_base or "vllm" in api_base.lower()):
            ptype = ProviderType.CUSTOM
        elif api_base:
            # Custom proxy endpoints (apmix.ai, deepseek.com, together.xyz, etc.)
            # speak OpenAI-compatible /chat/completions protocol
            ptype = ProviderType.CUSTOM
        elif "openrouter" in model.lower():
            ptype = ProviderType.OPENROUTER
        elif "claude" in model.lower():
            ptype = ProviderType.ANTHROPIC
        elif "gpt" in model.lower() or "o1" in model.lower():
            ptype = ProviderType.OPENAI
        elif "gemini" in model.lower():
            ptype = ProviderType.GEMINI

        # Normalise bases (Claude Code forgiveness: accept full endpoint paste):
        # - Ollama `.../api` or bare host -> `.../v1` for ChatOpenAI
        # - OpenAI-compat full paths `.../v1/chat/completions`, `.../messages`,
        #   `.../responses` -> strip to `.../v1` (SDK appends the path itself).
        #   This was the live `openrouter/free` bug: curl uses the full URL and
        #   works, but ChatOpenAI with base `.../v1/chat/completions` double-appends
        #   to `.../chat/completions/chat/completions` and fails.
        norm_base = ProviderManager._normalize_base_for_provider(
            ptype, api_base.strip() if api_base and api_base.strip() else None
        )

        extra_headers = {}
        if ptype == ProviderType.OPENROUTER or (norm_base and "openrouter" in norm_base):
            extra_headers = {
                "HTTP-Referer": "https://github.com/rajboopathiking/ScopeForge",
                "X-Title": "ScopeForge Agent Harness",
            }

        cfg = LLMConfig(
            name=name,
            provider=ptype,
            model=model,
            api_key=api_key if api_key and api_key.strip() else None,
            api_base=norm_base,
            temperature=max(0.0, min(float(temperature), 2.0)),
            max_tokens=max(1, int(max_tokens or 4096)),
            extra_headers=extra_headers,
        )
        self.register_provider(cfg)
        # Track true customs so the picker never mislabels stock as [CUSTOM].
        # A stock name reused here is treated as a custom override.
        self.custom_provider_names.add(name)
        if name in self.providers:
            # register_provider already saved; ensure the custom set survives
            # even when the name collides with a stock preset.
            pass
        if set_active:
            self.set_active_provider(name)
        return cfg

    def update_active_config(
        self,
        model: Optional[str] = None,
        provider: Optional[str] = None,
        api_key: Optional[str] = None,
        api_base: Optional[str] = None,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
    ) -> LLMConfig:
        """Update active configuration parameters dynamically."""
        cfg = self.get_active_config().model_copy()
        if model is not None:
            m = model.strip()
            cfg.model = m
            if ":free" in m or "openrouter" in m or cfg.provider == ProviderType.OPENROUTER:
                cfg.extra_headers.setdefault("HTTP-Referer", "https://github.com/rajboopathiking/ScopeForge")
                cfg.extra_headers.setdefault("X-Title", "ScopeForge Agent Harness")
        if provider is not None:
            try:
                cfg.provider = ProviderType(provider.lower())
            except Exception:
                pass
        if api_key is not None:
            if api_key and str(api_key).strip().lower().startswith("env:"):
                cfg.api_key = self.resolve_key_ref(api_key)
            else:
                cleaned = self._sanitize_api_key(api_key)
                cfg.api_key = cleaned if cleaned else None
        if api_base is not None:
            b = api_base.strip() or None
            b = ProviderManager._normalize_base_for_provider(cfg.provider, b)
            cfg.api_base = b
        if temperature is not None:
            cfg.temperature = max(0.0, min(float(temperature), 2.0))
        if max_tokens is not None:
            cfg.max_tokens = max(1, int(max_tokens))

        self.providers[cfg.name] = cfg
        self._cached_chat_model = None
        self.save_config()
        return cfg

    def get_chat_model(self, force_refresh: bool = False) -> BaseChatModel:
        """Get or instantiate the active LangChain ChatModel."""
        if self._cached_chat_model is None or force_refresh:
            config = self.get_active_config()
            self._cached_chat_model = create_chat_model(config)
        return self._cached_chat_model
