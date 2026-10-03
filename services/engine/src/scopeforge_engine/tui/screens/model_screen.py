"""Model selection modal screen."""
from __future__ import annotations

from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, DataTable, Label

from ...llm_providers.manager import ProviderManager


class ModelPickerModal(ModalScreen[str]):
    """Modal screen for interactive LLM provider selection."""

    def __init__(self, provider_mgr: ProviderManager, **kwargs):
        super().__init__(**kwargs)
        self.provider_mgr = provider_mgr

    def compose(self) -> ComposeResult:
        with Vertical(id="modal-dialog"):
            yield Label("⚡ Select LLM Provider / Model", id="modal-title")
            with Vertical(id="modal-content"):
                yield DataTable(id="models-table")
            with Horizontal(id="modal-buttons"):
                yield Button("Select & Apply", variant="success", id="btn-select", classes="modal-btn")
                yield Button("Cancel (Esc)", variant="default", id="btn-cancel", classes="modal-btn")

    def on_mount(self):
        table = self.query_one("#models-table", DataTable)
        table.cursor_type = "row"
        table.add_columns("Active", "Name", "Provider", "Model ID", "Temperature")

        active_name = self.provider_mgr.active_provider_name
        for p in self.provider_mgr.list_providers():
            is_active = "● ACTIVE" if p.name == active_name else ""
            table.add_row(is_active, p.name, p.provider.value, p.model, str(p.temperature), key=p.name)

    def on_button_pressed(self, event: Button.Pressed):
        if event.button.id == "btn-select":
            table = self.query_one("#models-table", DataTable)
            if table.cursor_coordinate:
                row_key, _ = table.coordinate_to_cell_key(table.cursor_coordinate)
                selected_name = str(row_key.value)
                self.provider_mgr.set_active_provider(selected_name)
                self.dismiss(selected_name)
            else:
                self.dismiss(None)
        elif event.button.id == "btn-cancel":
            self.dismiss(None)

    def on_key(self, event):
        if event.key == "escape":
            self.dismiss(None)
