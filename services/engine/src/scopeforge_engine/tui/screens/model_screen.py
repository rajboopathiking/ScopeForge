"""Model and Provider configuration modal screen (Interactive UI Configuration)."""
from __future__ import annotations

from typing import Optional
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Button, DataTable, Input, Label, Select, TabbedContent, TabPane

from ...llm_providers.manager import ProviderManager
from ...llm_providers.models import LLMConfig, ProviderType


class ModelPickerModal(ModalScreen[str]):
    """Modal screen for interactive LLM provider selection and configuration in the TUI."""

    def __init__(self, provider_mgr: ProviderManager, **kwargs):
        super().__init__(**kwargs)
        self.provider_mgr = provider_mgr

    def compose(self) -> ComposeResult:
        with Vertical(id="modal-dialog"):
            yield Label("⚡ LLM Provider & Model Configuration Center", id="modal-title")
            with TabbedContent(initial="tab-select"):
                # TAB 1: Select Active Model
                with TabPane("📋 Active Models & Quick Switch", id="tab-select"):
                    yield Label("Select any registered model and click 'Activate Selected':", classes="tab-help")
                    yield DataTable(id="models-table")
                    with Horizontal(id="modal-buttons-select"):
                        yield Button("Activate Selected", variant="primary", id="btn-select", classes="modal-btn")
                        yield Button("Close (Esc)", variant="default", id="btn-cancel-select", classes="modal-btn")

                # TAB 2: Configure Provider & Model directly from UI
                with TabPane("⚙️ Configure Provider in UI", id="tab-config"):
                    with VerticalScroll(id="config-form-scroll"):
                        yield Label("🚀 Quick Presets (Click to Auto-Fill):", classes="form-section-title")
                        with Horizontal(id="preset-container"):
                            yield Button("OpenRouter Free", id="btn-pre-or-free", classes="preset-btn", variant="primary")
                            yield Button("DeepSeek R1 Free", id="btn-pre-deepseek", classes="preset-btn", variant="success")
                            yield Button("Llama 3.3 Free", id="btn-pre-llama", classes="preset-btn", variant="warning")
                            yield Button("Gemini 2.0 Free", id="btn-pre-gemini", classes="preset-btn", variant="default")
                            yield Button("Claude 3.5", id="btn-pre-claude", classes="preset-btn", variant="default")
                            yield Button("Local Ollama", id="btn-pre-ollama", classes="preset-btn", variant="default")

                        yield Label("Provider Platform:", classes="form-label")
                        yield Select(
                            options=[
                                ("OpenRouter (openrouter.ai)", "openrouter"),
                                ("Anthropic (claude direct)", "anthropic"),
                                ("OpenAI (gpt direct)", "openai"),
                                ("Ollama (local offline)", "ollama"),
                                ("Groq (ultra-fast cloud)", "groq"),
                                ("Custom / Local vLLM Endpoint", "custom"),
                            ],
                            value="openrouter",
                            id="sel-provider",
                        )

                        yield Label("Configuration Name:", classes="form-label")
                        yield Input(value="openrouter-free", placeholder="e.g. openrouter-free, my-custom-model", id="inp-cfg-name")

                        yield Label("Model ID / Identifier:", classes="form-label")
                        yield Input(value="openrouter/free", placeholder="e.g. openrouter/free, deepseek/deepseek-r1:free", id="inp-cfg-model")

                        yield Label("API Key (optional if already exported in environment):", classes="form-label")
                        yield Input(placeholder="Paste API Key here (or leave blank to use ENV)", password=True, id="inp-cfg-key")

                        yield Label("API Base URL:", classes="form-label")
                        yield Input(value="https://openrouter.ai/api/v1", placeholder="https://openrouter.ai/api/v1", id="inp-cfg-base")

                        with Horizontal(classes="form-row"):
                            with Vertical(classes="half-col"):
                                yield Label("Temperature (0.0 - 1.0):", classes="form-label")
                                yield Input(value="0.2", placeholder="0.2", id="inp-cfg-temp")
                            with Vertical(classes="half-col"):
                                yield Label("Max Tokens:", classes="form-label")
                                yield Input(value="4096", placeholder="4096", id="inp-cfg-tokens")

                        yield Label("", id="cfg-status-msg")

                        with Horizontal(id="modal-buttons-config"):
                            yield Button("💾 Save & Activate in UI", variant="primary", id="btn-save-activate", classes="modal-btn")
                            yield Button("Save to Config Only", variant="success", id="btn-save-only", classes="modal-btn")
                            yield Button("Close (Esc)", variant="default", id="btn-cancel-config", classes="modal-btn")

    def on_mount(self):
        self._refresh_models_table()

    def _refresh_models_table(self):
        table = self.query_one("#models-table", DataTable)
        table.clear(columns=True)
        table.cursor_type = "row"
        table.add_columns("Active", "Name", "Provider", "Model ID", "Temperature")

        active_name = self.provider_mgr.active_provider_name
        for p in self.provider_mgr.list_providers():
            is_active = "● ACTIVE" if p.name == active_name else ""
            table.add_row(is_active, p.name, p.provider.value, p.model, str(p.temperature), key=p.name)

    def _apply_preset(self, name: str, provider: str, model: str, base_url: str, temp: str):
        self.query_one("#inp-cfg-name", Input).value = name
        self.query_one("#inp-cfg-model", Input).value = model
        self.query_one("#inp-cfg-base", Input).value = base_url
        self.query_one("#inp-cfg-temp", Input).value = temp
        try:
            self.query_one("#sel-provider", Select).value = provider
        except Exception:
            pass
        status = self.query_one("#cfg-status-msg", Label)
        status.update(f"✓ Applied preset: {name} ({model})")

    def on_button_pressed(self, event: Button.Pressed):
        btn_id = event.button.id

        # Quick Presets
        if btn_id == "btn-pre-or-free":
            self._apply_preset("openrouter-free", "openrouter", "openrouter/free", "https://openrouter.ai/api/v1", "0.2")
        elif btn_id == "btn-pre-deepseek":
            self._apply_preset("openrouter-free-deepseek", "openrouter", "deepseek/deepseek-r1:free", "https://openrouter.ai/api/v1", "0.2")
        elif btn_id == "btn-pre-llama":
            self._apply_preset("openrouter-free-llama", "openrouter", "meta-llama/llama-3.3-70b-instruct:free", "https://openrouter.ai/api/v1", "0.1")
        elif btn_id == "btn-pre-gemini":
            self._apply_preset("openrouter-free-gemini", "openrouter", "google/gemini-2.0-flash-exp:free", "https://openrouter.ai/api/v1", "0.1")
        elif btn_id == "btn-pre-claude":
            self._apply_preset("openrouter-claude", "openrouter", "anthropic/claude-3.5-sonnet", "https://openrouter.ai/api/v1", "0.1")
        elif btn_id == "btn-pre-ollama":
            self._apply_preset("ollama-llama3", "ollama", "llama3:latest", "http://localhost:11434", "0.2")

        # Table Selection
        elif btn_id == "btn-select":
            table = self.query_one("#models-table", DataTable)
            if table.cursor_coordinate:
                row_key, _ = table.coordinate_to_cell_key(table.cursor_coordinate)
                selected_name = str(row_key.value)
                self.provider_mgr.set_active_provider(selected_name)
                self.dismiss(selected_name)
            else:
                self.dismiss(None)

        # Config Save / Activate
        elif btn_id in ("btn-save-activate", "btn-save-only"):
            name = self.query_one("#inp-cfg-name", Input).value.strip()
            model = self.query_one("#inp-cfg-model", Input).value.strip()
            sel_val = self.query_one("#sel-provider", Select).value
            provider_str = str(sel_val) if sel_val is not None else "openrouter"
            key = self.query_one("#inp-cfg-key", Input).value.strip() or None
            base = self.query_one("#inp-cfg-base", Input).value.strip() or None
            temp_str = self.query_one("#inp-cfg-temp", Input).value.strip() or "0.1"
            tokens_str = self.query_one("#inp-cfg-tokens", Input).value.strip() or "4096"

            if not name or not model:
                self.query_one("#cfg-status-msg", Label).update("❌ Configuration Name and Model ID are required!")
                return

            try:
                temp = float(temp_str)
            except ValueError:
                temp = 0.1

            try:
                tokens = int(tokens_str)
            except ValueError:
                tokens = 4096

            try:
                ptype = ProviderType(provider_str)
            except Exception:
                ptype = ProviderType.OPENROUTER

            extra_headers = {}
            if ptype == ProviderType.OPENROUTER:
                extra_headers = {
                    "HTTP-Referer": "https://github.com/rajboopathiking/ScopeForge",
                    "X-Title": "ScopeForge Agent Harness",
                }

            cfg = LLMConfig(
                name=name,
                provider=ptype,
                model=model,
                api_key=key,
                api_base=base,
                temperature=temp,
                max_tokens=tokens,
                extra_headers=extra_headers,
            )

            self.provider_mgr.register_provider(cfg)
            self._refresh_models_table()

            if btn_id == "btn-save-activate":
                self.provider_mgr.set_active_provider(name)
                self.dismiss(name)
            else:
                self.query_one("#cfg-status-msg", Label).update(f"✓ Saved '{name}' ({model}) to configuration registry.")

        elif btn_id in ("btn-cancel-select", "btn-cancel-config"):
            self.dismiss(None)

    def on_key(self, event):
        if event.key == "escape":
            self.dismiss(None)
