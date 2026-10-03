"""Human-in-the-Loop tool call approval modal screen."""
from __future__ import annotations

import json
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Label, Markdown, Static

from ...middleware.base import ToolApprovalRequest


class ApprovalModal(ModalScreen[bool]):
    """Modal screen for operator confirmation before sensitive tool execution."""

    def __init__(self, request: ToolApprovalRequest, **kwargs):
        super().__init__(**kwargs)
        self.request = request

    def compose(self) -> ComposeResult:
        with Vertical(id="modal-dialog"):
            yield Label("⚠️ Security Policy Gate: Operator Approval Required", id="modal-title")
            with Vertical(id="modal-content"):
                yield Static(
                    f"[bold red]Tool Requested:[/] [bold]{self.request.tool_name}[/]\n"
                    f"[bold cyan]Requesting Agent:[/] {self.request.agent_name}\n"
                    f"[bold yellow]Severity Level:[/] {self.request.severity}\n\n"
                    f"[bold]Reason:[/] {self.request.reason}\n\n"
                    f"[bold]Arguments Proposed:[/]\n"
                    f"```json\n{json.dumps(self.request.tool_args, indent=2)}\n```"
                )
            with Horizontal(id="modal-buttons"):
                yield Button("APPROVE & EXECUTE (Y)", variant="success", id="btn-approve", classes="modal-btn")
                yield Button("DENY / BLOCK (N)", variant="error", id="btn-deny", classes="modal-btn")

    def on_button_pressed(self, event: Button.Pressed):
        if event.button.id == "btn-approve":
            self.dismiss(True)
        elif event.button.id == "btn-deny":
            self.dismiss(False)

    def on_key(self, event):
        if event.key in ("y", "enter"):
            self.dismiss(True)
        elif event.key in ("n", "escape"):
            self.dismiss(False)
