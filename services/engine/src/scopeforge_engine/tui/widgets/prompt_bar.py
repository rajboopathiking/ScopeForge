"""Interactive prompt bar with slash command support, clipboard integration, and history recall."""
from __future__ import annotations

from typing import Callable, List, Optional
from textual.app import ComposeResult
from textual.containers import Horizontal
from textual.widget import Widget
from textual.widgets import Input, Label

from .clipboard_input import ClipboardInput

SLASH_COMMANDS = [
    ("/help", "Show interactive help modal & keyboard shortcuts"),
    ("/copy", "Copy last response, full session, code, or findings to system clipboard (/copy [all|code|findings])"),
    ("/paste", "Paste system clipboard into prompt input"),
    ("/model", "Switch LLM provider (e.g. /model gpt-4o, /model claude-3-7-sonnet)"),
    ("/mode", "Switch execution mode (/mode plan, /mode artifacts, /mode live)"),
    ("/agent", "Direct task to specific agent (/agent recon, /agent audit, /agent exploit)"),
    ("/a2a", "Inspect Agent-to-Agent message bus telemetry, team registry & collaboration (/a2a, /a2a send)"),
    ("/skill", "Install, create, view, or activate skills (/skill install <url>, /skill list, /skill <name>)"),
    ("/mcp", "Manage Model Context Protocol servers (/mcp add <name> <cmd>, /mcp remove <name>, /mcp list, /mcp tools)"),
    ("/config", "View or adjust runtime agent configurations"),
    ("/rag", "Query or ingest cybersecurity knowledge (/rag <query> or /rag ingest <file>)"),
    ("/wiki", "View or update user preferences and memory (/wiki <read|write>)"),
    ("/scope", "Add or view authorized target scopes (/scope add <target>)"),
    ("/status", "Display current session and agent telemetry status"),
    ("/cost", "Show token counters and estimated session cost"),
    ("/findings", "List all discovered security findings"),
    ("/report", "Export comprehensive SecOps report"),
    ("/init", "Initialize SCOPEFORGE.md project memory"),
    ("/diff", "Inspect git working tree diff"),
    ("/commit", "Commit changes with git"),
    ("/review", "Run Claude Code style review on code changes"),
    ("/compact", "Compact chat history to preserve context"),
    ("/doctor", "Run diagnostic health checks"),
    ("/pr", "Generate pull request summary"),
    ("/mouse", "Toggle terminal native mouse selection mode (F7)"),
    ("/search", "Perform live web search (/search <query>)"),
    ("/bash", "Run command in terminal sandbox (/bash <cmd> or $ <cmd>)"),
    ("/goal", "Autonomous multi-step task execution toward a stated goal"),
    ("/plan", "Plan out complex tasks step-by-step in safe PLAN mode"),
    ("/tasks", "List ongoing or background tasks"),
    ("/clear", "Clear chat history stream"),
    ("/quit", "Exit ScopeForge"),
]


class PromptInput(ClipboardInput):
    """Specialized input for PromptBar with history recall and tab completion."""

    def __init__(self, prompt_bar: PromptBar, **kwargs):
        super().__init__(**kwargs)
        self.prompt_bar = prompt_bar

    def on_key(self, event) -> None:
        if event.key == "up":
            recalled = self.prompt_bar.recall_history(-1)
            if recalled is not None:
                self.value = recalled
                self.cursor_position = len(recalled)
                event.stop()
                event.prevent_default()
        elif event.key == "down":
            recalled = self.prompt_bar.recall_history(1)
            if recalled is not None:
                self.value = recalled
                self.cursor_position = len(recalled)
                event.stop()
                event.prevent_default()
        elif event.key == "tab":
            val = self.value.strip()
            if val.startswith("/"):
                matches = [cmd for cmd, _ in SLASH_COMMANDS if cmd.startswith(val)]
                if len(matches) == 1:
                    self.value = matches[0] + " "
                    self.cursor_position = len(self.value)
                    event.stop()
                    event.prevent_default()


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
            yield PromptInput(
                self,
                placeholder="Ask cybersecurity agents, run recon, audit code, or type / for commands...",
                id="prompt-input",
            )
        yield Label(
            "[dim]Commands: /help  /model  /mode  /copy  /mouse  /agent  /skill  /mcp  /config  /rag  /export  /clear[/]",
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

    def recall_history(self, delta: int) -> Optional[str]:
        if not self.history:
            return None
        new_index = self.history_index + delta
        if new_index < 0:
            self.history_index = 0
            return self.history[0]
        elif new_index >= len(self.history):
            self.history_index = len(self.history)
            return ""
        else:
            self.history_index = new_index
            return self.history[self.history_index]

    def paste_clipboard_content(self) -> None:
        try:
            inp = self.query_one("#prompt-input", PromptInput)
            inp.action_paste()
        except Exception:
            pass

    def focus_input(self):
        try:
            self.query_one("#prompt-input", Input).focus()
        except Exception:
            pass
