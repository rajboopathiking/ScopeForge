"""Claude Code / Open Code style fast model switcher & custom model configuration modal."""
from __future__ import annotations

from typing import List, Optional, Tuple
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Input, Label, OptionList, Select
from textual.widgets.option_list import Option

from ...llm_providers.manager import ProviderManager
from ...llm_providers.models import LLMConfig, ProviderType


MODEL_PRESETS = [
    ("openrouter-free", "OpenRouter Free (openrouter/free)", "[FREE] [AUTO]"),
    ("openrouter-free-nemotron", "Nemotron 550B Free (nvidia/...:free)", "[FREE] [ULTRA-FAST]"),
    ("openrouter-free-apodex", "Apodex 1.1 Mini Free (apodex/apodex-1.1-mini:free)", "[FREE] [FAST]"),
    ("openrouter-free-qwen", "Qwen 3.8 27B Free (qwen/qwen3.8-27b:free)", "[FREE] [CODING]"),
    ("openrouter-claude", "Claude 3.5 Sonnet (anthropic/claude-3.5-sonnet)", "[FRONTIER]"),
    ("gpt-4o", "GPT-4o (openai/gpt-4o)", "[FRONTIER]"),
    ("groq-llama3", "Groq Llama 3.3 (llama-3.3-70b-versatile)", "[ULTRA-FAST]"),
    ("ollama-llama3", "Local Ollama (llama3:latest)", "[LOCAL] [OFFLINE]"),
]


class ModelPickerModal(ModalScreen[str]):
    """Claude Code / Open Code style fast model switcher and in-terminal configuration modal."""

    def __init__(self, provider_mgr: ProviderManager, show_custom: bool = False, **kwargs):
        super().__init__(**kwargs)
        self.provider_mgr = provider_mgr
        self.show_custom_form = show_custom
        self.option_targets: List[str] = []

    def compose(self) -> ComposeResult:
        with Vertical(id="model-picker-dialog"):
            yield Label("⚡ Select or Configure Model", id="modal-title")

            # LIST VIEW (Default fast model switcher)
            with Vertical(id="view-list", classes="" if not self.show_custom_form else "-hidden"):
                yield Label("Navigate with ↑/↓ + Enter, or press 1-9 to select directly:", classes="modal-hint")
                yield OptionList(id="model-option-list")
                with Horizontal(id="modal-buttons-select"):
                    yield Button("Select (Enter)", variant="primary", id="btn-select", classes="modal-btn")
                    yield Button("Add Custom (9)", variant="success", id="btn-go-custom", classes="modal-btn")
                    yield Button("Cancel (Esc)", variant="default", id="btn-cancel", classes="modal-btn")

            # CUSTOM CONFIG VIEW — api_key, base_url, model_name are essential
            with Vertical(id="view-custom", classes="-hidden" if not self.show_custom_form else ""):
                yield Label("⚙️ Add Custom Model — api_key, base_url, model_name are required (*):", classes="form-section-title")

                yield Label("Alias / Name (optional, auto-generated if blank):", classes="form-label")
                yield Input(placeholder="e.g. my-custom-model, deepseek-v3", id="inp-cfg-name")

                yield Label("Provider Platform *:", classes="form-label")
                yield Select(
                    [
                        ("Auto-detect from model/base_url", "auto"),
                        ("OpenAI-compatible", "openai"),
                        ("Anthropic", "anthropic"),
                        ("OpenRouter", "openrouter"),
                        ("Groq", "groq"),
                        ("Gemini (native)", "gemini"),
                        ("Ollama (local, no key needed)", "ollama"),
                        ("Custom / vLLM / LM Studio", "custom"),
                    ],
                    value="auto",
                    id="inp-cfg-provider",
                )

                yield Label("Model Name / ID *:", classes="form-label")
                yield Input(placeholder="e.g. deepseek/deepseek-chat, gpt-4o, meta-llama/llama-3.3-70b-instruct (required)", id="inp-cfg-model")

                yield Label("API Key * (required except Ollama/local — or use env:VAR):", classes="form-label")
                yield Input(placeholder="sk-... / gsk-... / sk-or-v1-... (required)", password=True, id="inp-cfg-key")

                yield Label("API Base URL *:", classes="form-label")
                yield Input(placeholder="e.g. https://api.deepseek.com/v1, https://openrouter.ai/api/v1, http://localhost:11434/v1 (required)", id="inp-cfg-base")

                yield Label("Sampling Temperature (optional, default 0.2):", classes="form-label")
                yield Input(value="0.2", placeholder="0.2", id="inp-cfg-temp")

                yield Label("Max Tokens (optional, default 4096):", classes="form-label")
                yield Input(value="4096", placeholder="4096", id="inp-cfg-max")

                yield Label("", id="cfg-status-msg")

                with Horizontal(id="modal-buttons-config"):
                    yield Button("Save & Select", variant="primary", id="btn-save-activate", classes="modal-btn")
                    yield Button("Test", variant="success", id="btn-test-custom", classes="modal-btn")
                    yield Button("Back", variant="default", id="btn-back-to-list", classes="modal-btn")
                    yield Button("Cancel", variant="default", id="btn-cancel-custom", classes="modal-btn")

    def on_mount(self):
        self._populate_options()

    def _populate_options(self):
        opt_list = self.query_one("#model-option-list", OptionList)
        opt_list.clear_options()

        active_name = self.provider_mgr.active_provider_name
        self.option_targets = []

        # 1. Presets (1 to 8)
        preset_ids = set()
        for idx, (m_id, label, tag) in enumerate(MODEL_PRESETS, start=1):
            preset_ids.add(m_id)
            # Exact match only. Previous `active_name in m_id` flagged
            # `openrouter-free` active when `openrouter-free-deepseek` was set (and vice versa).
            is_active = "● [ACTIVE] " if m_id == active_name else ""
            display = f"[{idx}] {is_active}{label}  {tag}"
            opt_list.add_option(Option(display, id=m_id))
            self.option_targets.append(m_id)

        # 2. Custom Configured Models
        custom_list = [
            (name, cfg) for name, cfg in self.provider_mgr.providers.items()
            if name not in preset_ids and name != "mock-secops"
        ]

        curr_idx = len(self.option_targets) + 1
        for name, cfg in custom_list:
            is_active = "● [ACTIVE] " if name == active_name else ""
            display = f"[{curr_idx}] {is_active}{name} ({cfg.model})  [CUSTOM]"
            opt_list.add_option(Option(display, id=name))
            self.option_targets.append(name)
            curr_idx += 1

        # 3. Add Custom Model Action
        custom_shortcut = f"[{curr_idx}]" if curr_idx <= 9 else "[+]"
        opt_list.add_option(Option(f"{custom_shortcut} ➕ Add Custom Model (Name, Model, API Key, Base URL)...", id="action-custom"))
        self.option_targets.append("action-custom")

        # Highlight active model if in list
        highlight_idx = 0
        for i, tid in enumerate(self.option_targets):
            if tid == active_name:
                highlight_idx = i
                break
        opt_list.highlighted = highlight_idx

    def on_option_list_option_selected(self, event: OptionList.OptionSelected):
        opt_id = str(event.option_id)
        if opt_id == "action-custom":
            self._switch_to_custom(True)
        else:
            self.provider_mgr.set_active_provider(opt_id)
            self.dismiss(opt_id)

    def _switch_to_custom(self, to_custom: bool):
        self.show_custom_form = to_custom
        view_list = self.query_one("#view-list", Vertical)
        view_custom = self.query_one("#view-custom", Vertical)
        if to_custom:
            view_list.add_class("-hidden")
            view_custom.remove_class("-hidden")
            try:
                self.query_one("#inp-cfg-name", Input).focus()
            except Exception:
                pass
        else:
            view_custom.add_class("-hidden")
            view_list.remove_class("-hidden")
            self._populate_options()
            try:
                self.query_one("#model-option-list", OptionList).focus()
            except Exception:
                pass

    def on_button_pressed(self, event: Button.Pressed):
        btn_id = event.button.id

        if btn_id == "btn-select":
            opt_list = self.query_one("#model-option-list", OptionList)
            if opt_list.highlighted is not None:
                option = opt_list.get_option_at_index(opt_list.highlighted)
                opt_id = str(option.id)
                if opt_id == "action-custom":
                    self._switch_to_custom(True)
                else:
                    self.provider_mgr.set_active_provider(opt_id)
                    self.dismiss(opt_id)
            else:
                self.dismiss(None)

        elif btn_id == "btn-go-custom":
            self._switch_to_custom(True)

        elif btn_id == "btn-back-to-list":
            self._switch_to_custom(False)

        elif btn_id in ("btn-cancel", "btn-cancel-custom"):
            self.dismiss(None)

        elif btn_id == "btn-save-activate":
            self._save_and_activate_custom()

        elif btn_id == "btn-test-custom":
            self._test_custom_config()

    def on_input_submitted(self, event: Input.Submitted):
        """Allow pressing Enter inside custom configuration inputs to save & activate immediately."""
        self._save_and_activate_custom()

    def _read_custom_inputs(self) -> Tuple[str, str, Optional[str], str, str, float, int]:
        """Read raw custom-form inputs. Returns (name, model, key, base, provider, temp, max_tokens)."""
        name = self.query_one("#inp-cfg-name", Input).value.strip()
        model = self.query_one("#inp-cfg-model", Input).value.strip()
        key = self.query_one("#inp-cfg-key", Input).value.strip() or None
        base = self.query_one("#inp-cfg-base", Input).value.strip()
        try:
            provider_sel = self.query_one("#inp-cfg-provider", Select)
            provider = str(provider_sel.value or "auto").strip().lower()
        except Exception:
            provider = "auto"
        temp_str = self.query_one("#inp-cfg-temp", Input).value.strip() or "0.2"
        try:
            max_str = self.query_one("#inp-cfg-max", Input).value.strip() or "4096"
        except Exception:
            max_str = "4096"
        try:
            temp = max(0.0, min(float(temp_str), 2.0))
        except ValueError:
            temp = 0.2
        try:
            max_tokens = max(1, int(max_str))
        except ValueError:
            max_tokens = 4096
        # Auto-infer provider if auto
        if provider == "auto":
            if "openrouter" in model.lower() or (base and "openrouter" in base):
                provider = "openrouter"
            elif "groq" in model.lower() or (base and "groq" in base):
                provider = "groq"
            elif "claude" in model.lower() or (base and "anthropic" in base):
                provider = "anthropic"
            elif "ollama" in model.lower() or (base and "11434" in base):
                provider = "ollama"
            elif "gpt" in model.lower() or (base and "openai" in base):
                provider = "openai"

        # Auto-fill base_url if left blank for known platforms
        if not base:
            if provider == "openrouter" or "openrouter" in model.lower():
                base = "https://openrouter.ai/api/v1"
            elif provider == "openai":
                base = "https://api.openai.com/v1"
            elif provider == "groq":
                base = "https://api.groq.com/openai/v1"
            elif provider == "ollama":
                base = "http://localhost:11434/v1"
            elif provider == "anthropic":
                base = "https://api.anthropic.com/v1"

        # Auto-inherit key from manager if not provided
        if not key:
            import os
            if (provider == "openrouter" or "openrouter" in model.lower()) and os.getenv("OPENROUTER_API_KEY"):
                key = os.getenv("OPENROUTER_API_KEY")
            elif provider == "openai" and os.getenv("OPENAI_API_KEY"):
                key = os.getenv("OPENAI_API_KEY")
            elif provider == "groq" and os.getenv("GROQ_API_KEY"):
                key = os.getenv("GROQ_API_KEY")
            elif provider == "anthropic" and os.getenv("ANTHROPIC_API_KEY"):
                key = os.getenv("ANTHROPIC_API_KEY")
            else:
                for p in self.provider_mgr.providers.values():
                    if p.api_key and (p.provider.value == provider or (provider == "openrouter" and "openrouter" in str(p.api_base))):
                        key = p.api_key
                        break

        if not name and model:
            name = f"custom-{model.replace('/', '-').split(':')[0]}"
        return name, model, key, base, provider, temp, max_tokens

    def _validate_custom_inputs(
        self, name: str, model: str, key: Optional[str], base: str, provider: str
    ) -> Optional[str]:
        """Return error message if essential fields are missing, else None."""
        # model_name is essential
        if not model:
            return "✗ Model Name / ID * is required (e.g. openrouter/free, deepseek/deepseek-chat, gpt-4o)"
        # base_url is essential
        if not base:
            return "✗ API Base URL * is required (e.g. https://openrouter.ai/api/v1, https://api.deepseek.com/v1)"
        if not (base.startswith("http://") or base.startswith("https://")):
            return f"✗ Invalid Base URL '{base}'. Must start with http(s)://"
        # api_key is essential except for local providers
        is_local = provider == "ollama" or "localhost" in base or "127.0.0.1" in base
        if not key and not is_local:
            return (
                "✗ API Key * is required (or use Ollama/local). "
                "Paste key or set via env (e.g. OPENROUTER_API_KEY)."
            )
        if key and key.lower().startswith("env:"):
            var = key[4:].strip()
            if not var:
                return "✗ API Key 'env:' must name a variable, e.g. env:OPENROUTER_API_KEY"
        return None

    def _set_status(self, msg: str) -> None:
        try:
            self.query_one("#cfg-status-msg", Label).update(msg)
        except Exception:
            pass

    def _test_custom_config(self) -> None:
        """Validate essential fields + instantiate chat model without saving."""
        name, model, key, base, provider, temp, max_tokens = self._read_custom_inputs()
        err = self._validate_custom_inputs(name, model, key, base, provider)
        if err:
            self._set_status(err)
            return
        # Resolve env:VAR without ever echoing the value
        resolved_key = key
        if key and key.lower().startswith("env:"):
            import os as _os

            var = key[4:].strip()
            resolved_key = _os.getenv(var)
            if not resolved_key:
                self._set_status(f"✗ env:{var} is not set in this environment")
                return
        try:
            from ...llm_providers.factory import create_chat_model
            from ...llm_providers.models import LLMConfig as _LLMConfig
            from ...llm_providers.models import ProviderType as _PT

            ptype = provider if provider != "auto" else None
            # Reuse manager inference when auto
            tmp_cfg = self.provider_mgr.add_custom_provider(
                name=f"__test__{name or 'tmp'}",
                model=model,
                api_key=resolved_key,
                api_base=base,
                provider=None if ptype in (None, "auto") else ptype,
                temperature=temp,
                max_tokens=max_tokens,
                set_active=False,
            )
            # Remove temp entry immediately (never persist test configs)
            self.provider_mgr.providers.pop(tmp_cfg.name, None)
            chat = create_chat_model(tmp_cfg)
            mock_marker = getattr(chat, "model_name", "")
            if isinstance(mock_marker, str) and mock_marker.startswith("[MOCK fallback"):
                self._set_status(f"⚠️ Config OK but provider would run offline: {mock_marker[:120]}")
            else:
                self._set_status(f"✓ Config valid: {tmp_cfg.provider.value}/{tmp_cfg.model} at {tmp_cfg.api_base}")
        except Exception as e:
            self._set_status(f"✗ Invalid config: {e}")

    def _save_and_activate_custom(self):
        name, model, key, base, provider, temp, max_tokens = self._read_custom_inputs()

        err = self._validate_custom_inputs(name, model, key, base, provider)
        if err:
            self._set_status(err)
            return

        # Resolve env:VAR reference at save time (store resolved? No — store None
        # and rely on env at runtime to avoid persisting secrets; but if user
        # explicitly pasted a key, store it as before). Here we keep pasted keys;
        # env:VAR stays as None with a hint since factory reads env automatically.
        save_key: Optional[str] = key
        if key and key.lower().startswith("env:"):
            save_key = None

        try:
            cfg = self.provider_mgr.add_custom_provider(
                name=name,
                model=model,
                api_key=save_key,
                api_base=base,
                provider=None if provider == "auto" else provider,
                temperature=temp,
                max_tokens=max_tokens,
                set_active=True,
            )
        except ValueError as e:
            self._set_status(f"✗ {e}")
            return
        except Exception as e:
            self._set_status(f"✗ Save failed: {e}")
            return
        self.dismiss(cfg.name)

    def on_key(self, event):
        if event.key == "escape":
            if self.show_custom_form:
                self._switch_to_custom(False)
            else:
                self.dismiss(None)
        elif not self.show_custom_form:
            if event.key in ("1", "2", "3", "4", "5", "6", "7", "8", "9"):
                idx = int(event.key) - 1
                if hasattr(self, "option_targets") and idx < len(self.option_targets):
                    target = self.option_targets[idx]
                    if target == "action-custom":
                        self._switch_to_custom(True)
                    else:
                        self.provider_mgr.set_active_provider(target)
                        self.dismiss(target)
            elif event.key in ("+", "c", "C"):
                self._switch_to_custom(True)
