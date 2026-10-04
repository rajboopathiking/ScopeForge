"""Chat log widget rendering conversation, markdown, tool cards, and A2A events."""
from __future__ import annotations

import json
import re
from typing import Any, Dict, List, Optional
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

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.last_agent_response: str = ""
        self.transcript: List[Dict[str, str]] = []
        self._stream_buffer: str = ""
        self._stream_full_content: str = ""
        self._current_agent: str = "supervisor"
        self._stream_active: bool = False

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
            "[dim]• Press [bold cyan]F6[/] (or /copy) to copy last agent response to system clipboard[/]\n"
        )
        log.write(banner)

    def add_user_message(self, text: str):
        if getattr(self, "_stream_active", False):
            self.finish_agent_stream()
        try:
            log = self.query_one("#chat-log", RichLog)
            log.write(f"\n[bold #58a6ff]❯ You:[/] {self._escape(text)}\n")
        except Exception:
            pass
        self.transcript.append({"role": "User", "content": text})

    def start_agent_stream(self, agent: str):
        """Begin streaming tokens for an agent response."""
        if getattr(self, "_stream_active", False):
            self.finish_agent_stream()
        color = {
            "supervisor": "#58a6ff",
            "recon": "#79c0ff",
            "audit": "#d29922",
            "exploit": "#f85149",
            "report": "#bc8cff",
            "dev": "#3fb950",
        }.get(agent.lower(), "#58a6ff")
        try:
            log = self.query_one("#chat-log", RichLog)
            log.write(f"[bold {color}]🤖 [{agent.capitalize()}Agent][/]")
        except Exception:
            pass
        self._current_agent = agent
        self._stream_buffer = ""
        self._stream_full_content = ""
        self._stream_active = True

    def append_agent_chunk(self, chunk: Any):
        """Append a streamed token chunk and write completed lines in real-time."""
        if not getattr(self, "_stream_active", False):
            self.start_agent_stream("supervisor")
        if not chunk:
            return
        if not isinstance(chunk, str):
            if isinstance(chunk, list):
                parts = []
                for p in chunk:
                    if isinstance(p, str):
                        parts.append(p)
                    elif isinstance(p, dict):
                        parts.append(str(p.get("text") or p.get("content") or ""))
                chunk = "".join(parts)
            else:
                chunk = str(chunk)
        if not chunk:
            return
        self._stream_buffer += chunk
        self._stream_full_content += chunk
        try:
            log = self.query_one("#chat-log", RichLog)
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
        except Exception:
            pass

    def finish_agent_stream(self):
        """Flush any remaining stream buffer and finalize agent message."""
        if not getattr(self, "_stream_active", False):
            return
        try:
            log = self.query_one("#chat-log", RichLog)
            if getattr(self, "_stream_buffer", ""):
                log.write(self._escape(self._stream_buffer))
                self._stream_buffer = ""
            log.write("")
        except Exception:
            pass
        self._stream_active = False
        full_text = self._stream_full_content.strip()
        if full_text:
            self.last_agent_response = full_text
            self.transcript.append({"role": f"{self._current_agent.capitalize()}Agent", "content": full_text})
        self._stream_full_content = ""

    def add_agent_message(self, agent: str, markdown_content: Any, record_as_last_response: bool = True):
        if getattr(self, "_stream_active", False):
            self.finish_agent_stream()
        if not isinstance(markdown_content, str):
            if isinstance(markdown_content, list):
                parts = []
                for p in markdown_content:
                    if isinstance(p, str):
                        parts.append(p)
                    elif isinstance(p, dict):
                        parts.append(str(p.get("text") or p.get("content") or ""))
                markdown_content = "".join(parts)
            else:
                markdown_content = str(markdown_content or "")
        color = {
            "supervisor": "#58a6ff",
            "recon": "#79c0ff",
            "audit": "#d29922",
            "exploit": "#f85149",
            "report": "#bc8cff",
            "dev": "#3fb950",
        }.get(agent.lower(), "#58a6ff")

        try:
            log = self.query_one("#chat-log", RichLog)
            log.write(f"[bold {color}]🤖 [{agent.capitalize()}Agent][/]")
            log.write(self._escape(markdown_content))
            log.write("")
        except Exception:
            pass

        # Don't overwrite last_agent_response if this is a clipboard notification or status
        is_clipboard_status = markdown_content.startswith("📋")
        if record_as_last_response and not is_clipboard_status:
            self.last_agent_response = markdown_content
        self.transcript.append({"role": f"{agent.capitalize()}Agent", "content": markdown_content})

    def add_tool_call(self, tool_name: str, args: Dict[str, Any], status: str = "RUNNING"):
        args_str = json.dumps(args, default=str)
        if len(args_str) > 80:
            args_str = args_str[:77] + "..."
        status_color = "green" if status == "SUCCESS" else ("red" if "BLOCK" in status else "yellow")
        try:
            log = self.query_one("#chat-log", RichLog)
            log.write(f"  [dim]⚙ Tool Call:[/] [bold]{tool_name}[/]({args_str}) -> [{status_color}][{status}][/]")
        except Exception:
            pass

    def add_a2a_banner(self, sender: str, recipient: str, intent: str, preview: str = ""):
        try:
            log = self.query_one("#chat-log", RichLog)
            log.write(
                f"  [#8957e5]⇄ A2A Protocol:[/] [bold]{self._escape(sender)}[/] → [bold]{self._escape(recipient)}[/] "
                f"[#d2a8ff][{self._escape(intent)}][/] [dim]{self._escape(preview)}[/]"
            )
        except Exception:
            pass

    def add_finding_card(self, finding: Dict[str, Any]):
        sev = finding.get("severity", "MEDIUM")
        color = {"CRITICAL": "red", "HIGH": "bright_red", "MEDIUM": "yellow"}.get(sev, "blue")
        title = self._escape(str(finding.get('title', '')))
        try:
            log = self.query_one("#chat-log", RichLog)
            log.write(
                f"\n  [bold {color} on #21262d] 🚨 VULNERABILITY FOUND: {title} [/]\n"
                f"  Severity: [{color} bold]{self._escape(str(sev))}[/] | CVSS: [bold]{self._escape(str(finding.get('cvss', 'N/A')))}[/] | CWE: {self._escape(str(finding.get('cwe', 'N/A')))}\n"
            )
        except Exception:
            pass

    def get_last_agent_response(self) -> str:
        """Return the most recent agent response text."""
        return self.last_agent_response

    def get_full_transcript(self) -> str:
        """Format the full conversation history as Markdown."""
        if not self.transcript:
            return "No conversation history recorded."
        parts = ["# ScopeForge Session Transcript\n"]
        for entry in self.transcript:
            role = entry.get("role", "Message")
            content = entry.get("content", "")
            parts.append(f"### {role}\n\n{content}\n")
        return "\n".join(parts)

    def get_last_code_block(self) -> str:
        """Extract the last code block (```...```) from the most recent agent response."""
        text = self.last_agent_response or ""
        matches = re.findall(r"```(?:\w+)?\n(.*?)```", text, re.DOTALL)
        if matches:
            return matches[-1].strip()
        return text.strip()
