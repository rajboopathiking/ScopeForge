"""LLM Wiki & User Preference Memory editor modal screen."""
from __future__ import annotations

from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Label, Select, TextArea

from ...wiki.store import SecWiki


class WikiModal(ModalScreen):
    """Modal screen for reading and updating persistent memory and user preferences."""

    def __init__(self, wiki: SecWiki, **kwargs):
        super().__init__(**kwargs)
        self.wiki = wiki
        self.current_page = "preferences"

    def compose(self) -> ComposeResult:
        with Vertical(id="modal-dialog"):
            yield Label("⚡ ScopeForge LLM Wiki & User Preference Memory", id="modal-title")
            with Horizontal(height=3):
                yield Label("Select Page: ", classes="modal-label")
                yield Select(
                    [(p, p) for p in self.wiki.list_pages()],
                    value="preferences",
                    id="wiki-page-select",
                )
            with Vertical(id="modal-content"):
                yield TextArea(id="wiki-editor")
            with Horizontal(id="modal-buttons"):
                yield Button("Save Page", variant="success", id="btn-save", classes="modal-btn")
                yield Button("Close (Esc)", variant="default", id="btn-close", classes="modal-btn")

    def on_mount(self):
        editor = self.query_one("#wiki-editor", TextArea)
        content = self.wiki.read_page("preferences")
        editor.load_text(content)

    def on_select_changed(self, event: Select.Changed):
        if event.value:
            self.current_page = str(event.value)
            editor = self.query_one("#wiki-editor", TextArea)
            content = self.wiki.read_page(self.current_page)
            editor.load_text(content)

    def on_button_pressed(self, event: Button.Pressed):
        if event.button.id == "btn-save":
            editor = self.query_one("#wiki-editor", TextArea)
            self.wiki.write_page(self.current_page, editor.text)
            self.notify(f"Saved wiki page '{self.current_page}' successfully.")
        elif event.button.id == "btn-close":
            self.dismiss()

    def on_key(self, event):
        if event.key == "escape":
            self.dismiss()
