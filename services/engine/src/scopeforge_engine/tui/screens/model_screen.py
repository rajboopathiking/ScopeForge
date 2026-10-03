"""Claude Code / Open Code style fast model switcher & configuration modal."""
from __future__ import annotations

from typing import List, Optional
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Input, Label, OptionList, Select
from textual.widgets.option_list import Option

from ...llm_providers.manager import ProviderManager
from ...llm_providers.models import LLMConfig, ProviderType


MODEL_PRESETS = [
    ("openrouter-free", "OpenRouter Free (openrouter/free)", "[FREE] [AUTO]"),
    ("openrouter-free-deepseek", "DeepSeek R1 Free (deepseek/deepseek-r1:free)", "[FREE] [REASONING]"),
    ("openrouter-free-llama", "Llama 3.3 70B Free (meta-llama/...:free)", "[FREE] [FAST]"),
    ("openrouter-free-gemini", "Gemini 2.0 Flash Free (google/...:free)", "[FREE] [FAST]"),
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

    def compose(self) -> ComposeResult:
        with Vertical(id="model-picker-dialog"):
            yield Label("⚡ Select or Configure Model", id="modal-title")

            # LIST VIEW (Default fast model switcher)
            with Vertical(id="view-list", classes="" if not self.show_custom_form else "-hidden"):
                yield Label("Navigate with ↑/↓ + Enter, or press 1-9 to select directly:", classes="modal-hint")
                yield OptionList(id="model-option-list")
                with Horizontal(id="modal-buttons-select"):
                    yield Button("Activate (Enter)", variant="primary", id="btn-select", classes="modal-btn")
                    yield Button("Custom (9)", variant="success", id="btn-go-custom", classes="modal-btn")
                    yield Button("Cancel (Esc)", variant="default", id="btn-cancel", classes="modal-btn")

            # CUSTOM CONFIG VIEW
            with Vertical(id="view-custom", classes="-hidden" if not self.show_custom_form else ""):
                yield Label("⚙️ Configure Custom Provider & Model in UI:", classes="form-section-title")

                yield Label("Provider Platform:", classes="form-label")
                yield Select(
                    options=[
                        ("OpenRouter (openrouter.ai)", "openrouter"),
                        ("Anthropic (claude direct)", "anthropic"),
                        ("OpenAI (gpt direct)", "openai"),
                        ("Ollama (local offline)", "ollama"),
                        ("Groq (ultra-fast cloud)", "groq"),
                        ("Custom / Local vLLM", "custom"),
                    ],
                    value="openrouter",
                    id="sel-provider",
                )

                yield Label("Model ID / Identifier:", classes="form-label")
                yield Input(value="openrouter/free", placeholder="e.g. openrouter/free, deepseek/deepseek-r1:free", id="inp-cfg-model")

                yield Label("API Key (optional if already exported in environment):", classes="form-label")
                yield Input(placeholder="Paste API Key here (or leave blank to use ENV)", password=True, id="inp-cfg-key")

                yield Label("API Base URL:", classes="form-label")
                yield Input(value="https://openrouter.ai/api/v1", placeholder="https://openrouter.ai/api/v1", id="inp-cfg-base")

                yield Label("Temperature (0.0 - 1.0):", classes="form-label")
                yield Input(value="0.2", placeholder="0.2", id="inp-cfg-temp")

                yield Label("", id="cfg-status-msg")

                with Horizontal(id="modal-buttons-config"):
                    yield Button("💾 Apply & Activate", variant="primary", id="btn-save-activate", classes="modal-btn")
                    yield Button("Back to Models", variant="default", id="btn-back-to-list", classes="modal-btn")
                    yield Button("Cancel (Esc)", variant="default", id="btn-cancel-custom", classes="modal-btn")

    def on_mount(self):
        self._populate_options()

    def _populate_options(self):
        opt_list = self.query_one("#model-option-list", OptionList)
        opt_list.clear_options()

        active_name = self.provider_mgr.active_provider_name

        for idx, (m_id, label, tag) in enumerate(MODEL_PRESETS, start=1):
            is_active = "● [ACTIVE] " if m_id == active_name or active_name in m_id else ""
            display = f"[{idx}] {is_active}{label}  {tag}"
            opt_list.add_option(Option(display, id=m_id))

        # Add option 9: Custom
        opt_list.add_option(Option("[9] ⚙️  Custom Provider & Model Settings...", id="action-custom"))
        opt_list.highlighted = 0

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
        else:
            view_custom.add_class("-hidden")
            view_list.remove_class("-hidden")
            self._populate_options()

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
            model = self.query_one("#inp-cfg-model", Input).value.strip() or "openrouter/free"
            sel_val = self.query_one("#sel-provider", Select).value
            provider_str = str(sel_val) if sel_val is not None else "openrouter"
            key = self.query_one("#inp-cfg-key", Input).value.strip() or None
            base = self.query_one("#inp-cfg-base", Input).value.strip() or None
            temp_str = self.query_one("#inp-cfg-temp", Input).value.strip() or "0.2"

            try:
                temp = float(temp_str)
            except ValueError:
                temp = 0.2

            try:
                ptype = ProviderType(provider_str)
            except Exception:
                ptype = ProviderType.OPENROUTER

            cfg_name = f"{ptype.value}-{model.replace('/', '-').split(':')[0]}"
            extra_headers = {}
            if ptype == ProviderType.OPENROUTER:
                extra_headers = {
                    "HTTP-Referer": "https://github.com/rajboopathiking/ScopeForge",
                    "X-Title": "ScopeForge Agent Harness",
                }

            cfg = LLMConfig(
                name=cfg_name,
                provider=ptype,
                model=model,
                api_key=key,
                api_base=base,
                temperature=temp,
                extra_headers=extra_headers,
            )

            self.provider_mgr.register_provider(cfg)
            self.provider_mgr.set_active_provider(cfg_name)
            self.dismiss(cfg_name)

    def on_key(self, event):
        if event.key == "escape":
            self.dismiss(None)
        elif not self.show_custom_form and event.key in ("1", "2", "3", "4", "5", "6", "7", "8"):
            idx = int(event.key) - 1
            if idx < len(MODEL_PRESETS):
                target_id = MODEL_PRESETS[idx][0]
                self.provider_mgr.set_active_provider(target_id)
                self.dismiss(target_id)
        elif not self.show_custom_form and event.key == "9":
            self._switch_to_custom(True)
