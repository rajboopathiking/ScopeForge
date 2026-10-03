"""Chat log widget rendering conversation, markdown, tool cards, and A2A events."""
from __future__ import annotations

import json
from typing import Any, Dict
from textual.app import ComposeResult
from textual.widget import Widget
from textual.widgets import RichLog


class ChatStream(Widget):
    """Main terminal stream displaying chat history, tool calls, and agent interactions."""

    DEFAULT_CSS = """
    ChatStream {
        width: 1fr;
        height: 100%;
        background: #0d1117;
        layout: vertical;
    }
    #chat-log {
        width: 100%;
        height: 100%;
        background: #0d1117;
        padding: 1 2;
        overflow-y: scroll;
    }
    """

    def compose(self) -> ComposeResult:
        yield RichLog(id="chat-log", wrap=True, highlight=True, markup=True)

    def on_mount(self):
        self.post_welcome_banner()

    @staticmethod
    def _escape(text: str) -> str:
        # RichLog with markup=True treats [tag] as style. LLM markdown and code
        # are full of brackets — escape them so general chat renders verbatim
        # like Open Code / Claude Code instead of dropping content.
        return text.replace("[", "\\[")

    def post_welcome_banner(self):
        log = self.query_one("#chat-log", RichLog)
        banner = (
            "[bold #58a6ff]╭─────────────────────────────────────────────────────────────────────────────╮[/]\n"
            "[bold #58a6ff]│  ⚡ SCOPEFORGE — Cybersecurity Multi-Agent Terminal Harness                │[/]\n"
            "[bold #58a6ff]│  LangChain • LangGraph • LlamaIndex RAG • LLM Wiki • MCP • A2A Protocol    │[/]\n"
            "[bold #58a6ff]╰─────────────────────────────────────────────────────────────────────────────╯[/]\n\n"
            "[dim]• Mode: [bold green]PLAN[/] (Safe exploration, 0 target packets)[/]\n"
            "[dim]• Type [bold cyan]/help[/] for commands or press [bold cyan]F1[/] for keyboard shortcuts.[/]\n"
            "[dim]• Press [bold cyan]F2[/] to toggle sidebar | [bold cyan]F3[/] to cycle mode | [bold cyan]F4[/] to open Wiki[/]\n"
        )
        log.write(banner)

    def add_user_message(self, text: str):
        if getattr(self, "_stream_active", False):
            self.finish_agent_stream()
        log = self.query_one("#chat-log", RichLog)
        log.write(f"\n[bold #58a6ff]❯ You:[/] {self._escape(text)}\n")

    def start_agent_stream(self, agent: str):
        """Begin streaming tokens for an agent response."""
        if getattr(self, "_stream_active", False):
            self.finish_agent_stream()
        log = self.query_one("#chat-log", RichLog)
        color = {
            "supervisor": "#58a6ff",
            "recon": "#79c0ff",
            "audit": "#d29922",
            "exploit": "#f85149",
            "report": "#bc8cff",
            "dev": "#3fb950",
        }.get(agent.lower(), "#58a6ff")
        log.write(f"[bold {color}]🤖 [{agent.capitalize()}Agent][/]")
        self._stream_buffer = ""
        self._stream_active = True

    def append_agent_chunk(self, chunk: str):
        """Append a streamed token chunk and write completed lines in real-time."""
        if not getattr(self, "_stream_active", False):
            self.start_agent_stream("supervisor")
        if not chunk:
            return
        log = self.query_one("#chat-log", RichLog)
        self._stream_buffer += chunk
        while "\n" in self._stream_buffer:
            line, self._stream_buffer = self._stream_buffer.split("\n", 1)
            log.write(self._escape(line))
        # Long line streaming flush (words flow continuously without waiting for newline)
        if len(self._stream_buffer) > 80:
            space_idx = self._stream_buffer.rfind(" ")
            if space_idx > 30:
                line = self._stream_buffer[:space_idx]
                self._stream_buffer = self._stream_buffer[space_idx + 1 :]
                log.write(self._escape(line))

    def finish_agent_stream(self):
        """Flush any remaining stream buffer and finalize agent message."""
        if not getattr(self, "_stream_active", False):
            return
        log = self.query_one("#chat-log", RichLog)
        if getattr(self, "_stream_buffer", ""):
            log.write(self._escape(self._stream_buffer))
            self._stream_buffer = ""
        log.write("")
        self._stream_active = False

    def add_agent_message(self, agent: str, markdown_content: str):
        if getattr(self, "_stream_active", False):
            self.finish_agent_stream()
        log = self.query_one("#chat-log", RichLog)
        color = {
            "supervisor": "#58a6ff",
            "recon": "#79c0ff",
            "audit": "#d29922",
            "exploit": "#f85149",
            "report": "#bc8cff",
            "dev": "#3fb950",
        }.get(agent.lower(), "#58a6ff")

        log.write(f"[bold {color}]🤖 [{agent.capitalize()}Agent][/]")
        log.write(self._escape(markdown_content))
        log.write("")

    def add_tool_call(self, tool_name: str, args: Dict[str, Any], status: str = "RUNNING"):
        log = self.query_one("#chat-log", RichLog)
        args_str = json.dumps(args, default=str)
        if len(args_str) > 80:
            args_str = args_str[:77] + "..."
        status_color = "green" if status == "SUCCESS" else ("red" if "BLOCK" in status else "yellow")
        log.write(f"  [dim]⚙ Tool Call:[/] [bold]{tool_name}[/]({args_str}) -> [{status_color}][{status}][/]")

    def add_a2a_banner(self, sender: str, recipient: str, intent: str, preview: str = ""):
        log = self.query_one("#chat-log", RichLog)
        log.write(
            f"  [#8957e5]⇄ A2A Protocol:[/] [bold]{self._escape(sender)}[/] → [bold]{self._escape(recipient)}[/] "
            f"[#d2a8ff][{self._escape(intent)}][/] [dim]{self._escape(preview)}[/]"
        )

    def add_finding_card(self, finding: Dict[str, Any]):
        log = self.query_one("#chat-log", RichLog)
        sev = finding.get("severity", "MEDIUM")
        color = {"CRITICAL": "red", "HIGH": "bright_red", "MEDIUM": "yellow"}.get(sev, "blue")
        title = self._escape(str(finding.get('title', '')))
        log.write(
            f"\n  [bold {color} on #21262d] 🚨 VULNERABILITY FOUND: {title} [/]\n"
            f"  Severity: [{color} bold]{self._escape(str(sev))}[/] | CVSS: [bold]{self._escape(str(finding.get('cvss', 'N/A')))}[/] | CWE: {self._escape(str(finding.get('cwe', 'N/A')))}\n"
        )
