"""Custom Input widgets with integrated macOS and OS system clipboard support."""
from __future__ import annotations

from typing import Optional
from textual import events
from textual.actions import SkipAction
from textual.binding import Binding
from textual.widgets import Input

from ..clipboard import (
    clean_pasted_text,
    copy_to_system_clipboard,
    paste_from_system_clipboard,
)


class ClipboardInput(Input):
    """Input widget that reliably synchronizes with OS system clipboard (macOS/Linux/Windows)."""

    BINDINGS = [
        *Input.BINDINGS,
        Binding("super+v", "paste", "Paste from OS clipboard", show=False),
        Binding("ctrl+v", "paste", "Paste from OS clipboard", show=False),
        Binding("ctrl+shift+v", "paste", "Paste from OS clipboard", show=False),
        Binding("super+c", "copy", "Copy to OS clipboard", show=False),
        Binding("ctrl+c", "copy", "Copy to OS clipboard", show=False),
    ]

    def action_paste(self) -> None:
        """Paste from system clipboard or local fallback."""
        text = paste_from_system_clipboard()
        if not text and hasattr(self.app, "clipboard"):
            text = str(self.app.clipboard or "")
        if text:
            cleaned = clean_pasted_text(text, single_line=True)
            start, end = self.selection
            self.replace(cleaned, start, end)

    def action_copy(self) -> None:
        """Copy selected text to both system clipboard and Textual."""
        selected_text = self.selected_text
        if selected_text:
            copy_to_system_clipboard(selected_text)
            if hasattr(self.app, "copy_to_clipboard"):
                self.app.copy_to_clipboard(selected_text)
            if hasattr(self.app, "notify"):
                self.app.notify("✓ Copied selection to clipboard", title="ScopeForge Clipboard")
        else:
            # Fall back to screen text selection if user highlighted text in terminal
            try:
                screen_selected = self.screen.get_selected_text()
                if screen_selected:
                    copy_to_system_clipboard(screen_selected)
                    if hasattr(self.app, "copy_to_clipboard"):
                        self.app.copy_to_clipboard(screen_selected)
                    if hasattr(self.app, "notify"):
                        self.app.notify("✓ Copied selection to clipboard", title="ScopeForge Clipboard")
                    return
            except Exception:
                pass
            raise SkipAction()

    def _on_paste(self, event: events.Paste) -> None:
        """Handle bracketed paste event emitted by terminal emulators."""
        if event.text:
            cleaned = clean_pasted_text(event.text, single_line=True)
            selection = self.selection
            if selection.is_empty:
                self.insert_text_at_cursor(cleaned)
            else:
                self.replace(cleaned, *selection)
        event.stop()
