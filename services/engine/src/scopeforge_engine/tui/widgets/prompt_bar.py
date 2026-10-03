"""Interactive prompt bar with slash command support and history recall."""
from __future__ import annotations

from typing import Callable, List, Optional
from textual.app import ComposeResult
from textual.containers import Horizontal
from textual.widget import Widget
from textual.widgets import Input, Label

SLASH_COMMANDS = [
    ("/help", "Show interactive help modal & keyboard shortcuts"),
    ("/model", "Switch LLM provider (e.g. /model gpt-4o, /model claude-3-7-sonnet)"),
    ("/mode", "Switch execution mode (/mode plan, /mode artifacts, /mode live)"),
    ("/agent", "Direct task to specific agent (/agent recon, /agent audit, /agent exploit)"),
    ("/skill", "View or activate specialized skills (/skill list, /skill <name>)"),
    ("/mcp", "List and manage Model Context Protocol servers (/mcp list, /mcp add)"),
    ("/config", "View or adjust runtime agent configurations"),
    ("/rag", "Query or ingest cybersecurity knowledge (/rag <query> or /rag ingest <file>)"),
    ("/wiki", "View or update user preferences and memory (/wiki <read|write>)"),
    ("/scope", "Add or view authorized target scopes (/scope add <target>)"),
    ("/status", "Display current session and agent telemetry status"),
    ("/cost", "Show token counters and estimated session cost"),
    ("/findings", "List all discovered security findings"),
    ("/report", "Export comprehensive SecOps report"),
    ("/clear", "Clear chat history stream"),
    ("/quit", "Exit ScopeForge"),
]


class PromptBar(Widget):
    """Input prompt container with prefix symbol, text input, and command hints."""

    DEFAULT_CSS = """
    PromptBar {
        dock: bottom;
        height: 4;
        width: 100%;
        background: #161b22;
        border-top: heavy #30363d;
        layout: vertical;
        padding: 0 1;
    }
    #prompt-row {
        height: 3;
        width: 100%;
        layout: horizontal;
        align-vertical: middle;
    }
    #prompt-symbol {
        width: 3;
        color: #58a6ff;
        text-style: bold;
    }
    #prompt-input {
        width: 1fr;
        height: 1;
        background: #0d1117;
        border: none;
        color: #f0f6fc;
    }
    #footer-bar {
        height: 1;
        width: 100%;
        color: #6e7681;
    }
    """

    def __init__(self, on_submit_callback: Optional[Callable[[str], None]] = None, **kwargs):
        super().__init__(**kwargs)
        self.on_submit_callback = on_submit_callback
        self.history: List[str] = []
        self.history_index: int = -1

    def compose(self) -> ComposeResult:
        with Horizontal(id="prompt-row"):
            yield Label("❯ ", id="prompt-symbol")
            yield Input(
                placeholder="Ask cybersecurity agents, run recon, audit code, or type / for commands...",
                id="prompt-input",
            )
        yield Label(
            "[dim]Commands: /help  /model  /mode  /agent  /skill  /mcp  /config  /rag  /wiki  /status  /cost  /clear[/]",
            id="footer-bar",
        )

    def on_input_submitted(self, event: Input.Submitted):
        value = event.value.strip()
        if not value:
            return

        self.history.append(value)
        self.history_index = len(self.history)
        event.input.value = ""

        if self.on_submit_callback:
            self.on_submit_callback(value)

    def focus_input(self):
        try:
            self.query_one("#prompt-input", Input).focus()
        except Exception:
            pass
