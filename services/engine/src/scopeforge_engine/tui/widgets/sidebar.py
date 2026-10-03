"""Sidebar widget with tabs for Multi-Agent, A2A feed, ScopeGate, Wiki, RAG, and MCP."""
from __future__ import annotations

import json
from typing import Any, Dict, List
from textual.app import ComposeResult
from textual.widget import Widget
from textual.widgets import (
    Button,
    DataTable,
    Input,
    Label,
    RichLog,
    Static,
    TabbedContent,
    TabPane,
)

from ...a2a.protocol import A2AMessage
from ...agents.custom_agent import CustomAgentLoader
from ...mcp_bridge import MCPBridge
from ...rag.engine import LlamaSecRAG
from ...skills.manager import SkillManager
from ...wiki.store import SecWiki


class SidebarWidget(Widget):
    """Multi-panel sidebar for agent observation, policy inspection, and memory."""

    DEFAULT_CSS = """
    SidebarWidget {
        width: 44;
        height: 100%;
        background: #161b22;
        border-left: solid #30363d;
        layout: vertical;
    }
    SidebarWidget.-hidden {
        display: none;
    }
    #sidebar-tabs {
        height: 100%;
        background: #161b22;
    }
    .sidebar-content {
        padding: 1;
        height: auto;
    }
    """

    def __init__(
        self,
        wiki: SecWiki,
        rag: LlamaSecRAG,
        mcp: MCPBridge,
        skill_manager: Optional[SkillManager] = None,
        **kwargs,
    ):
        super().__init__(**kwargs)
        self.wiki = wiki
        self.rag = rag
        self.mcp = mcp
        self.skill_mgr = skill_manager or SkillManager()
        self.custom_loader = CustomAgentLoader()

    def compose(self) -> ComposeResult:
        with TabbedContent(id="sidebar-tabs"):
            # Tab 1: Skills & Capabilities
            with TabPane("Skills", id="tab-skills"):
                yield Label("[bold cyan]Specialized Agent Skills[/]")
                skills_text = "\n\n".join(
                    f"• [bold green]{s.name}[/]: {s.description[:45]}...\n"
                    f"  [dim]triggers: {', '.join(s.triggers[:3])}[/]"
                    for s in self.skill_mgr.list_skills()
                )
                yield Static(skills_text, id="skills-list", classes="sidebar-content")
                yield Label("\n[dim]Use `/skill <name>` to activate[/]")

            # Tab 2: Agents & A2A
            with TabPane("Agents & A2A", id="tab-agents"):
                yield Label("[bold cyan]Active Multi-Agent Team[/]")
                yield Static(
                    "• [bold green]Supervisor[/]: Routing\n"
                    "• [bold blue]ReconAgent[/]: Perimeter\n"
                    "• [bold yellow]AuditAgent[/]: SAST & CVE\n"
                    "• [bold red]ExploitAgent[/]: PoC\n"
                    "• [bold magenta]ReportAgent[/]: CVSS\n"
                    "• [bold cyan]CloudSecAgent[/]: IAM\n"
                    "• [bold cyan]ApiSecAgent[/]: API",
                    classes="sidebar-content",
                )
                yield Label("\n[bold cyan]Live A2A Message Stream[/]")
                yield RichLog(id="a2a-log-stream", highlight=True, markup=True)

            # Tab 2: Scope & Rules
            with TabPane("Scope & Policy", id="tab-scope"):
                yield Label("[bold cyan]ScopeGate Policy Enforcer[/]")
                yield Static(
                    "[bold]Execution Modes:[/]\n"
                    " [green]• PLAN[/]: Passive (0 packets)\n"
                    " [yellow]• ARTIFACTS[/]: Local files\n"
                    " [red]• LIVE[/]: Bounded authorized\n\n"
                    "[bold green]Authorized Targets:[/]\n"
                    " - authorized.example\n"
                    " - *.example.com\n"
                    " - localhost (8080, 8443)\n"
                    " - 127.0.0.1\n\n"
                    "[bold red]Forbidden Boundaries:[/]\n"
                    " - *.gov, *.mil\n"
                    " - production.bank.com\n",
                    classes="sidebar-content",
                )

            # Tab 3: Wiki & Memory
            with TabPane("Wiki & Memory", id="tab-wiki"):
                yield Label("[bold cyan]User Preference Memory[/]")
                yield Static(id="wiki-preview", classes="sidebar-content")

            # Tab 4: RAG Knowledge
            with TabPane("RAG Store", id="tab-rag"):
                yield Label("[bold cyan]LlamaIndex Security Store[/]")
                yield Static(
                    f"• Indexed Docs: {len(self.rag.documents)}\n"
                    "• Categories: OWASP, CVE, RoE\n"
                    "• Vector/Lexical Engine: Online",
                    classes="sidebar-content",
                )
                yield Label("\n[bold cyan]RAG Query Preview[/]")
                yield Input(placeholder="Search security store...", id="rag-quick-query")
                yield RichLog(id="rag-results-log", highlight=True, markup=True)

            # Tab 5: MCP Servers
            with TabPane("MCP Protocol", id="tab-mcp"):
                yield Label("[bold cyan]Model Context Protocol[/]")
                mcp_text = "\n".join(
                    f"• [bold]{s['name']}[/]: {'[green]ENABLED[/]' if s['enabled'] else '[dim]DISABLED[/]'}\n  cmd: {s['command'][:25]}..."
                    for s in self.mcp.list_servers()
                )
                yield Static(mcp_text or "No MCP servers configured.", classes="sidebar-content")

            # Tab 6: Findings
            with TabPane("Findings", id="tab-findings"):
                yield Label("[bold cyan]Security Findings Ledger[/]")
                yield RichLog(id="findings-log", highlight=True, markup=True)

    def on_mount(self):
        self.update_wiki_preview()

    def update_wiki_preview(self):
        try:
            prefs = self.wiki.read_page("preferences")
            self.query_one("#wiki-preview", Static).update(prefs[:400] + "...")
        except Exception:
            pass

    def add_a2a_log(self, msg: A2AMessage):
        try:
            stream = self.query_one("#a2a-log-stream", RichLog)
            intent_color = {
                "TASK_DELEGATION": "cyan",
                "TASK_RESULT": "green",
                "EVIDENCE_SHARING": "yellow",
                "CONSENSUS_REQUEST": "magenta",
                "HANDOVER": "blue",
                "ALERT": "red",
            }.get(msg.intent.value, "white")
            line = (
                f"[dim]{msg.timestamp.split('T')[1][:8]}[/] "
                f"[bold]{msg.sender}[/] -> [bold]{msg.recipient}[/] "
                f"[{intent_color}][{msg.intent.value}][/]\n"
                f"[dim]{json.dumps(msg.payload)[:80]}[/]\n"
            )
            stream.write(line)
        except Exception:
            pass

    def add_finding(self, finding: Dict[str, Any]):
        try:
            f_log = self.query_one("#findings-log", RichLog)
            sev = finding.get("severity", "MEDIUM")
            color = {"CRITICAL": "red", "HIGH": "bright_red", "MEDIUM": "yellow", "LOW": "green"}.get(sev, "white")
            line = (
                f"[{color} bold][{sev}][/] [bold]{finding.get('id', 'FIND')}[/]: {finding.get('title')}\n"
                f"CVSS: {finding.get('cvss', 'N/A')} | CWE: {finding.get('cwe', 'N/A')}\n"
            )
            f_log.write(line)
        except Exception:
            pass
