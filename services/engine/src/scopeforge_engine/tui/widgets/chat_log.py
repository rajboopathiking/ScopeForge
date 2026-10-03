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
        log = self.query_one("#chat-log", RichLog)
        log.write(f"\n[bold #58a6ff]❯ You:[/] {text}\n")

    def add_agent_message(self, agent: str, markdown_content: str):
        log = self.query_one("#chat-log", RichLog)
        color = {
            "supervisor": "#58a6ff",
            "recon": "#79c0ff",
            "audit": "#d29922",
            "exploit": "#f85149",
            "report": "#bc8cff",
        }.get(agent.lower(), "#58a6ff")

        log.write(f"[bold {color}]🤖 [{agent.capitalize()}Agent][/]")
        log.write(markdown_content)
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
            f"  [#8957e5]⇄ A2A Protocol:[/] [bold]{sender}[/] → [bold]{recipient}[/] "
            f"[#d2a8ff][{intent}][/] [dim]{preview}[/]"
        )

    def add_finding_card(self, finding: Dict[str, Any]):
        log = self.query_one("#chat-log", RichLog)
        sev = finding.get("severity", "MEDIUM")
        color = {"CRITICAL": "red", "HIGH": "bright_red", "MEDIUM": "yellow"}.get(sev, "blue")
        log.write(
            f"\n  [bold {color} on #21262d] 🚨 VULNERABILITY FOUND: {finding.get('title')} [/]\n"
            f"  Severity: [{color} bold]{sev}[/] | CVSS: [bold]{finding.get('cvss', 'N/A')}[/] | CWE: {finding.get('cwe', 'N/A')}\n"
        )
