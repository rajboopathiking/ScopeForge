"""Main Textual Application for ScopeForge TUI (Claude Code / Open Code style)."""
from __future__ import annotations

import asyncio
from typing import Any, Dict, List, Optional
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical

from ..a2a.bus import A2ABus
from ..a2a.protocol import A2AMessage
from ..agents.graph import MultiAgentSecOpsOrchestrator
from ..llm_providers.manager import ProviderManager
from ..mcp_bridge import MCPBridge
from ..middleware.pipeline import MiddlewarePipeline, create_default_pipeline
from ..rag.engine import LlamaSecRAG
from ..skills.manager import SkillManager
from ..wiki.store import SecWiki
from .screens.approval_screen import ApprovalModal
from .screens.help_screen import HelpModal
from .screens.model_screen import ModelPickerModal
from .screens.wiki_screen import WikiModal
from .styles import TCSS_STYLES
from .widgets.chat_log import ChatStream
from .widgets.header_bar import HeaderBar
from .widgets.prompt_bar import PromptBar
from .widgets.sidebar import SidebarWidget


class ScopeForgeTUIApp(App):
    """ScopeForge Cybersecurity Agent Harness TUI."""

    CSS = TCSS_STYLES
    TITLE = "ScopeForge ⚡ Cybersecurity Agent Harness"
    SUB_TITLE = "Claude Code / Open Code style TUI"

    BINDINGS = [
        Binding("f1", "show_help", "Help", show=True),
        Binding("f2", "toggle_sidebar", "Sidebar", show=True),
        Binding("f3", "cycle_mode", "Cycle Mode", show=True),
        Binding("f4", "open_wiki", "Wiki Memory", show=True),
        Binding("f5", "clear_chat", "Clear", show=True),
        Binding("ctrl+c", "quit_app", "Quit", show=True),
    ]

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        # Initialize Subsystems
        self.provider_mgr = ProviderManager()
        self.wiki = SecWiki()
        self.rag = LlamaSecRAG()
        self.mcp = MCPBridge()
        self.a2a_bus = A2ABus()
        self.skill_mgr = SkillManager()

        self.sec_mode = "plan"
        self.current_scope = ["authorized.example", "*.example.com", "localhost"]
        self.pipeline = create_default_pipeline(mode=self.sec_mode, authorized_scopes=self.current_scope)

        self.orchestrator = MultiAgentSecOpsOrchestrator(
            provider_manager=self.provider_mgr,
            pipeline=self.pipeline,
            rag=self.rag,
            wiki=self.wiki,
            a2a_bus=self.a2a_bus,
            skill_manager=self.skill_mgr,
        )

        self.chat_history: List[Any] = []
        self.total_tokens: int = 0
        self.estimated_cost: float = 0.0

    def compose(self) -> ComposeResult:
        yield HeaderBar(id="header-bar")
        with Horizontal(id="workspace-container"):
            yield ChatStream(id="chat-stream")
            yield SidebarWidget(wiki=self.wiki, rag=self.rag, mcp=self.mcp, skill_manager=self.skill_mgr, id="sidebar")
        yield PromptBar(on_submit_callback=self.handle_user_input, id="prompt-bar")

    def on_mount(self):
        # Wire up A2A live notifications to sidebar & chat
        self.a2a_bus.subscribe(self._on_a2a_event)
        self._sync_header()
        try:
            self.query_one("#prompt-input").focus()
        except Exception:
            pass

    def _sync_header(self):
        try:
            header = self.query_one(HeaderBar)
            header.model_name = self.provider_mgr.active_provider_name
            header.mode = self.sec_mode
            header.scope = self.current_scope[0] if self.current_scope else "None"
            header.tokens = self.total_tokens
            header.cost = self.estimated_cost
        except Exception:
            pass

    def _on_a2a_event(self, msg: A2AMessage):
        # Update sidebar
        try:
            sidebar = self.query_one(SidebarWidget)
            sidebar.add_a2a_log(msg)
        except Exception:
            pass
        # Update chat stream
        try:
            chat = self.query_one(ChatStream)
            chat.add_a2a_banner(msg.sender, msg.recipient, msg.intent.value, str(msg.payload.get("task", ""))[:40])
        except Exception:
            pass

    def handle_user_input(self, text: str):
        text = text.strip()
        if not text:
            return

        # Check for slash commands
        if text.startswith("/"):
            self.execute_slash_command(text)
        else:
            self.run_agent_task(text)

    def execute_slash_command(self, cmd: str):
        parts = cmd.split(maxsplit=2)
        action = parts[0].lower()
        chat = self.query_one(ChatStream)

        if action in ("/help", "/?"):
            self.action_show_help()

        elif action == "/model":
            if len(parts) > 1:
                target = parts[1]
                if self.provider_mgr.set_active_provider(target):
                    chat.add_agent_message("Supervisor", f"✓ Switched active LLM model to **{self.provider_mgr.active_provider_name}**.")
                    self._sync_header()
                else:
                    chat.add_agent_message("Supervisor", f"✗ Provider '{target}' not found. Available: {', '.join([p.name for p in self.provider_mgr.list_providers()])}")
            else:
                self.action_pick_model()

        elif action == "/mode":
            if len(parts) > 1 and parts[1].lower() in ("plan", "artifacts", "live"):
                self.sec_mode = parts[1].lower()
                self.pipeline.middlewares[1].set_mode(self.sec_mode)
                chat.add_agent_message("Supervisor", f"✓ Switched execution mode to **{self.sec_mode.upper()}**.")
                self._sync_header()
            else:
                chat.add_agent_message("Supervisor", "Usage: `/mode <plan|artifacts|live>`")

        elif action == "/agent":
            if len(parts) > 1:
                agent_name = parts[1]
                chat.add_agent_message("Supervisor", f"✓ Directing next mission specifically to **{agent_name.capitalize()}Agent**.")
                header = self.query_one(HeaderBar)
                header.active_agent = agent_name
            else:
                chat.add_agent_message("Supervisor", "Active Agents: `supervisor`, `recon`, `audit`, `exploit`, `report`, `cloudsec`, `apisec`")

        elif action == "/rag":
            if len(parts) > 1:
                subcmd = parts[1]
                if subcmd.lower() == "ingest" and len(parts) > 2:
                    file_path = parts[2]
                    res = self.rag.ingest_file(file_path)
                    chat.add_agent_message("Supervisor", f"📚 **RAG Ingestion:** {res}")
                else:
                    query_str = " ".join(parts[1:])
                    results = self.rag.query(query_str, top_k=3)
                    lines = [f"- **({r['metadata'].get('category', 'SecOps')})**: {r['text'][:150]}..." for r in results]
                    chat.add_agent_message("Supervisor", f"📚 **RAG Knowledge Hits for '{query_str}':**\n\n" + "\n".join(lines))
            else:
                chat.add_agent_message("Supervisor", "Usage: `/rag <query>` or `/rag ingest <path>`")

        elif action == "/wiki":
            self.action_open_wiki()

        elif action in ("/skill", "/skills"):
            if len(parts) > 1 and parts[1].lower() != "list":
                skill_name = parts[1].lower()
                if self.skill_mgr.get_skill(skill_name):
                    if skill_name in self.skill_mgr.active_skills:
                        self.skill_mgr.deactivate_skill(skill_name)
                        chat.add_agent_message("Supervisor", f"Deactivated skill: **[{skill_name}]**")
                    else:
                        self.skill_mgr.activate_skill(skill_name)
                        chat.add_agent_message("Supervisor", f"✓ Activated specialized skill: **[{skill_name}]**")
                else:
                    chat.add_agent_message("Supervisor", f"Skill '{skill_name}' not found. Available: {', '.join([s.name for s in self.skill_mgr.list_skills()])}")
            else:
                skills_list = "\n".join(
                    f"- **[{s.name}]**: {s.description}\n  *Triggers:* `{', '.join(s.triggers)}` | *Status:* {'[ACTIVE]' if s.name in self.skill_mgr.active_skills else '[AVAILABLE]'}"
                    for s in self.skill_mgr.list_skills()
                )
                chat.add_agent_message("Supervisor", f"🧠 **Discovered Agent Skills (`.scopeforge/skills/`):**\n\n{skills_list}\n\n*Type `/skill <name>` to toggle activation.*")

        elif action == "/mcp":
            if len(parts) > 2 and parts[1].lower() == "add":
                # /mcp add <name> <command>
                mcp_parts = parts[2].split(maxsplit=1)
                if len(mcp_parts) == 2:
                    srv_name, srv_cmd = mcp_parts
                    try:
                        self.mcp.registry.add_server(srv_name, srv_cmd)
                        self.mcp.enable_server(srv_name)
                        chat.add_agent_message("Supervisor", f"✓ Added and enabled MCP server: **{srv_name}** (`{srv_cmd}`)")
                    except Exception as e:
                        chat.add_agent_message("Supervisor", f"❌ Error adding MCP server: {e}")
                else:
                    chat.add_agent_message("Supervisor", "Usage: `/mcp add <name> <command>`")
            elif len(parts) > 2 and parts[1].lower() == "enable":
                srv_name = parts[2].strip()
                try:
                    self.mcp.enable_server(srv_name)
                    chat.add_agent_message("Supervisor", f"✓ Enabled MCP server: **{srv_name}**")
                except Exception as e:
                    chat.add_agent_message("Supervisor", f"❌ Error: {e}")
            elif len(parts) > 2 and parts[1].lower() == "disable":
                srv_name = parts[2].strip()
                try:
                    self.mcp.disable_server(srv_name)
                    chat.add_agent_message("Supervisor", f"✓ Disabled MCP server: **{srv_name}**")
                except Exception as e:
                    chat.add_agent_message("Supervisor", f"❌ Error: {e}")
            else:
                servers = self.mcp.list_servers()
                s_list = "\n".join(f"- **{s['name']}**: {'[ENABLED]' if s['enabled'] else '[DISABLED]'} (`{s['command']}`)" for s in servers)
                chat.add_agent_message("Supervisor", f"🔌 **Model Context Protocol (MCP) Servers:**\n\n{s_list}\n\n*Commands: `/mcp add <name> <cmd>`, `/mcp enable <name>`, `/mcp disable <name>`*")

        elif action == "/config":
            active_cfg = self.provider_mgr.get_active_config()
            cfg_text = (
                "⚙️ **Active ScopeForge Configuration:**\n\n"
                f"- **Model**: `{active_cfg.name}` ({active_cfg.provider.value} / `{active_cfg.model}`)\n"
                f"- **Temperature**: `{active_cfg.temperature}`\n"
                f"- **Max Tokens**: `{active_cfg.max_tokens}`\n"
                f"- **Execution Mode**: `{self.sec_mode.upper()}`\n"
                f"- **Authorized Scopes**: `{', '.join(self.current_scope)}`\n"
                f"- **RAG Store**: LlamaIndex ({len(self.rag.documents)} documents indexed)\n"
                f"- **Active Skills**: `{', '.join(self.skill_mgr.active_skills) or 'None (auto-detection active)'}`\n"
                f"- **Audit Logging**: `.scopeforge/audit.jsonl` (Active SHA256 chain)\n"
            )
            chat.add_agent_message("Supervisor", cfg_text)

        elif action == "/cost":
            cost_text = (
                "📊 **Session Token Usage & Telemetry:**\n\n"
                f"- **Total Estimated Tokens**: `{self.total_tokens:,}`\n"
                f"- **Estimated Session Cost**: `${self.estimated_cost:.4f}`\n"
                f"- **Current Active Model**: `{self.provider_mgr.active_provider_name}`\n"
            )
            chat.add_agent_message("Supervisor", cost_text)

        elif action == "/status":
            status_text = (
                "⚡ **ScopeForge Mission Status:**\n\n"
                f"- **Execution Mode**: `{self.sec_mode.upper()}` (ScopeGate Enforced)\n"
                f"- **Active Model**: `{self.provider_mgr.active_provider_name}`\n"
                f"- **Primary Target**: `{self.current_scope[0] if self.current_scope else 'None'}`\n"
                f"- **Agent Team**: `Supervisor`, `ReconAgent`, `AuditAgent`, `ExploitAgent`, `ReportAgent`\n"
                f"- **A2A Bus**: Online ({len(self.a2a_bus.messages)} events recorded)\n"
                f"- **MCP Servers**: `{len(self.mcp.list_servers())}` connected\n"
                f"- **Skills Discovered**: `{len(self.skill_mgr.list_skills())}`\n"
            )
            chat.add_agent_message("Supervisor", status_text)

        elif action == "/scope":
            if len(parts) > 2 and parts[1].lower() == "add":
                target = parts[2]
                self.current_scope.append(target)
                self.pipeline.middlewares[1].add_scope(target)
                chat.add_agent_message("Supervisor", f"🛡 **ScopeGate:** Added `{target}` to authorized scope targets.")
                self._sync_header()
            else:
                chat.add_agent_message("Supervisor", f"🛡 **Authorized Target Scopes:**\n" + "\n".join(f"- `{s}`" for s in self.current_scope))

        elif action == "/findings":
            chat.add_agent_message("Supervisor", "📋 **Discovered Findings:**\n- `FIND-001` [CRITICAL] SQL Injection (CVSS 9.8)\n- `FIND-002` [MEDIUM] Missing Content-Security-Policy\n- `FIND-003` [MEDIUM] Wildcard CORS Header Exposure")

        elif action == "/report":
            self.run_agent_task("Compile full SecOps assessment report with executive summary and CVSS scores.")

        elif action == "/clear":
            self.action_clear_chat()

        elif action == "/quit":
            self.exit()

        else:
            chat.add_agent_message("Supervisor", f"Unknown command: `{action}`. Type `/help` for available commands.")

    def run_agent_task(self, prompt: str):
        """Invoke the LangGraph multi-agent system asynchronously."""
        chat = self.query_one(ChatStream)
        chat.add_user_message(prompt)

        async def _execute():
            header = self.query_one(HeaderBar)
            header.active_agent = "Supervisor"

            try:
                state = await self.orchestrator.run(
                    user_message=prompt,
                    mode=self.sec_mode,
                    scope=self.current_scope,
                    history=self.chat_history,
                )

                active_agent = state.get("active_agent", "Supervisor")
                header.active_agent = active_agent

                # Add last response
                if state.get("messages"):
                    last_msg = state["messages"][-1]
                    chat.add_agent_message(active_agent, str(last_msg.content))
                    self.chat_history.extend(state["messages"][-2:])

                # Update findings in sidebar
                sidebar = self.query_one(SidebarWidget)
                for f in state.get("findings", []):
                    sidebar.add_finding(f)
                    chat.add_finding_card(f)

                # Update token counters (estimated)
                self.total_tokens += len(prompt.split()) * 4 + 450
                self.estimated_cost += 0.003
                self._sync_header()

            except Exception as e:
                chat.add_agent_message("Supervisor", f"❌ Error during multi-agent orchestration: {e}")

        asyncio.create_task(_execute())

    def action_show_help(self):
        self.push_screen(HelpModal())

    def action_pick_model(self):
        def _on_model_picked(model_name: Optional[str]):
            if model_name:
                self._sync_header()
                chat = self.query_one(ChatStream)
                chat.add_agent_message("Supervisor", f"✓ Switched active model to **{model_name}**.")
        self.push_screen(ModelPickerModal(self.provider_mgr), _on_model_picked)

    def action_toggle_sidebar(self):
        sidebar = self.query_one("#sidebar")
        sidebar.toggle_class("-hidden")

    def action_cycle_mode(self):
        modes = ["plan", "artifacts", "live"]
        idx = modes.index(self.sec_mode)
        self.sec_mode = modes[(idx + 1) % len(modes)]
        self.pipeline.middlewares[1].set_mode(self.sec_mode)
        self._sync_header()
        chat = self.query_one(ChatStream)
        chat.add_agent_message("Supervisor", f"🛡 **Execution Mode:** Switched to **{self.sec_mode.upper()}**.")

    def action_open_wiki(self):
        self.push_screen(WikiModal(self.wiki))

    def action_clear_chat(self):
        log = self.query_one("#chat-log", RichLog)
        log.clear()
        chat = self.query_one(ChatStream)
        chat.post_welcome_banner()
        self.chat_history.clear()

    def action_quit_app(self):
        self.exit()


def main():
    app = ScopeForgeTUIApp()
    app.run()


if __name__ == "__main__":
    main()
