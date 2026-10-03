"""Claude Code / Open Code style fast model switcher & custom model configuration modal."""
from __future__ import annotations

from typing import List, Optional
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Input, Label, OptionList
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

            # CUSTOM CONFIG VIEW
            with Vertical(id="view-custom", classes="-hidden" if not self.show_custom_form else ""):
                yield Label("⚙️ Add Custom Model Configuration:", classes="form-section-title")

                yield Label("Model Name / Alias:", classes="form-label")
                yield Input(placeholder="e.g. my-custom-model, deepseek-v3", id="inp-cfg-name")

                yield Label("Model Identifier / ID:", classes="form-label")
                yield Input(value="openrouter/free", placeholder="e.g. deepseek/deepseek-chat, gpt-4o, mistral-large", id="inp-cfg-model")

                yield Label("API Key:", classes="form-label")
                yield Input(placeholder="Paste API Key here (or leave blank to use ENV)", password=True, id="inp-cfg-key")

                yield Label("API Base URL:", classes="form-label")
                yield Input(value="https://openrouter.ai/api/v1", placeholder="e.g. https://api.deepseek.com/v1, http://localhost:8000/v1", id="inp-cfg-base")

                yield Label("Sampling Temperature (optional, default 0.2):", classes="form-label")
                yield Input(value="0.2", placeholder="0.2", id="inp-cfg-temp")

                yield Label("", id="cfg-status-msg")

                with Horizontal(id="modal-buttons-config"):
                    yield Button("Save & Select", variant="primary", id="btn-save-activate", classes="modal-btn")
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
            is_active = "● [ACTIVE] " if m_id == active_name or active_name in m_id else ""
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

    def on_input_submitted(self, event: Input.Submitted):
        """Allow pressing Enter inside custom configuration inputs to save & activate immediately."""
        self._save_and_activate_custom()

    def _save_and_activate_custom(self):
        name = self.query_one("#inp-cfg-name", Input).value.strip()
        model = self.query_one("#inp-cfg-model", Input).value.strip() or "openrouter/free"
        key = self.query_one("#inp-cfg-key", Input).value.strip() or None
        base = self.query_one("#inp-cfg-base", Input).value.strip() or None
        temp_str = self.query_one("#inp-cfg-temp", Input).value.strip() or "0.2"

        if not name:
            name = f"custom-{model.replace('/', '-').split(':')[0]}"

        try:
            temp = float(temp_str)
        except ValueError:
            temp = 0.2

        cfg = self.provider_mgr.add_custom_provider(
            name=name,
            model=model,
            api_key=key,
            api_base=base,
            temperature=temp,
            set_active=True,
        )
        self.dismiss(name)

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
