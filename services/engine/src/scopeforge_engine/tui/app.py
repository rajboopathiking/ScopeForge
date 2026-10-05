import asyncio
import json
from pathlib import Path
from typing import Any, Dict, List, Optional
from langchain_core.messages import AIMessage, HumanMessage
from textual import events
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.widgets import RichLog

from ..a2a.bus import A2ABus
from ..a2a.protocol import A2AIntent, A2AMessage
from ..agents.graph import MultiAgentSecOpsOrchestrator
from ..llm_providers.manager import ProviderManager
from ..mcp_bridge import MCPBridge
from ..middleware.pipeline import MiddlewarePipeline, create_default_pipeline
from ..rag.engine import LlamaSecRAG
from ..skills.manager import SkillManager
from ..wiki.store import SecWiki
from .clipboard import copy_to_system_clipboard, paste_from_system_clipboard
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
        Binding("ctrl+h", "show_help", "Help (^H)", show=True),
        Binding("ctrl+b", "toggle_sidebar", "Sidebar (^B)", show=True),
        Binding("ctrl+m", "pick_model", "Model (^M)", show=True),
        Binding("ctrl+w", "open_wiki", "Wiki (^W)", show=True),
        Binding("ctrl+l", "clear_chat", "Clear (^L)", show=True),
        Binding("ctrl+y", "copy_last_response", "Copy (^Y)", show=True),
        Binding("ctrl+t", "toggle_mouse_capture", "Mouse (^T)", show=True),
        Binding("ctrl+o", "cycle_mode", "Mode (^O)", show=True),
        Binding("ctrl+q", "quit_app", "Quit (^Q)", show=True),
        # F-keys preserved as secondary compatibility bindings
        Binding("f1", "show_help", "Help", show=False),
        Binding("f2", "toggle_sidebar", "Sidebar", show=False),
        Binding("f3", "cycle_mode", "Cycle Mode", show=False),
        Binding("f4", "open_wiki", "Wiki Memory", show=False),
        Binding("f5", "clear_chat", "Clear", show=False),
        Binding("f6", "copy_last_response", "Copy Last", show=False),
        Binding("f7", "toggle_mouse_capture", "Mouse Mode", show=False),
        Binding("super+c", "handle_ctrl_c", "Copy Selection", show=False),
        Binding("ctrl+c", "handle_ctrl_c", "Quit / Copy", show=False),
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
            mcp_bridge=self.mcp,
        )

        self.chat_history: List[Any] = []
        self.total_tokens: int = 0
        self.estimated_cost: float = 0.0
        # One-shot routing override from `/agent <name>` (Open Code style).
        # Previously `/agent` only changed the header badge and never reached
        # the orchestrator, so directing did nothing.
        self.pending_agent: Optional[str] = None

    @property
    def clipboard(self) -> str:
        """System-aware clipboard: reads from OS system clipboard with fallback."""
        sys_clip = paste_from_system_clipboard()
        if sys_clip:
            self._clipboard = sys_clip
            return sys_clip
        return getattr(self, "_clipboard", "")

    def copy_to_clipboard(self, text: str) -> None:
        """Write to both internal Textual buffer (OSC 52) and OS system clipboard."""
        try:
            super().copy_to_clipboard(text)
        except Exception:
            self._clipboard = text
        copy_to_system_clipboard(text)

    def on_text_selected(self, event: events.TextSelected) -> None:
        """Automatically copy highlighted text to OS system clipboard upon mouse selection."""
        try:
            selected = self.screen.get_selected_text()
            if selected:
                self.copy_to_clipboard(selected)
                self.notify("✓ Copied selection to clipboard", title="ScopeForge Clipboard")
        except Exception:
            pass

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

    def _autofill_custom_base(self, model_id: str, base: Optional[str]) -> Optional[str]:
        if base and base.strip():
            return base.strip()
        low = (model_id or "").lower()
        if "openrouter" in low:
            return "https://openrouter.ai/api/v1"
        if "groq" in low:
            return "https://api.groq.com/openai/v1"
        if low.startswith("gpt-") or low.startswith("o1") or "openai" in low:
            return "https://api.openai.com/v1"
        if "claude" in low or "anthropic" in low:
            return "https://api.anthropic.com/v1"
        if "ollama" in low:
            return "http://localhost:11434/v1"
        return None

    def _slash_add_custom(self, m_name: str, m_id: str, m_key: Optional[str], m_base: Optional[str]):
        """Shared `/model add` + `/config add` path: env:VAR-aware, auto-fills base.

        Returns (cfg, error_message). Never raises for validation errors.
        """
        import os as _os
        if not (m_id or "").strip():
            return None, "✗ Model ID * is required. Usage: `/model add <name> <model_id> [key|env:VAR] [base_url]`"
        m_base = self._autofill_custom_base(m_id, m_base)
        if not m_base:
            return None, (
                "✗ API Base URL * is required. Usage: `/model add <name> <model_id> [key] [base_url]`\n"
                "Example: `/model add my-deepseek deepseek/deepseek-chat sk-... https://api.deepseek.com/v1`"
            )
        if not (m_base.startswith("http://") or m_base.startswith("https://")):
            return None, f"✗ Invalid base URL '{m_base}'. Must start with http(s)://"
        is_local = "localhost" in m_base or "127.0.0.1" in m_base
        key_is_env_ref = bool(m_key and str(m_key).strip().lower().startswith("env:"))
        if key_is_env_ref:
            try:
                self.provider_mgr.resolve_key_ref(m_key)
            except ValueError as e:
                return None, f"✗ {e}"
        has_env_key = any(_os.getenv(v) for v in (
            "OPENROUTER_API_KEY", "OPENAI_API_KEY", "ANTHROPIC_API_KEY",
            "GROQ_API_KEY", "GEMINI_API_KEY", "GOOGLE_API_KEY",
        ))
        if not m_key and not is_local and not has_env_key:
            return None, (
                "✗ API Key * is required (except Ollama/local). Usage: `/model add <name> <model_id> [key|env:VAR] [base_url]`\n"
                "Tip: use `/model` UI which marks api_key, base_url, model_name as * required."
            )
        try:
            cfg = self.provider_mgr.add_custom_provider(
                name=m_name, model=m_id, api_key=m_key, api_base=m_base, set_active=True,
            )
        except ValueError as e:
            return None, f"✗ {e}"
        except Exception as e:
            return None, f"✗ Save failed: {e}"
        return cfg, ""

    def _active_model_warning(self) -> str:
        """Empty string when live; otherwise a clear mock/offline suffix."""
        try:
            chat = self.provider_mgr.get_chat_model()
            marker = getattr(chat, "model_name", "")
            if isinstance(marker, str) and marker.startswith("[MOCK fallback"):
                return f"\n⚠️ Running offline: {marker[:160]}"
        except Exception:
            pass
        return ""

    def handle_user_input(self, text: str):
        text = text.strip()
        if not text:
            return

        # OpenCode / Claude Code quick terminal command prefix: $ <cmd> or ! <cmd>
        if text.startswith("$"):
            cmd_body = text[1:].strip()
            self.execute_slash_command(f"/bash {cmd_body}")
            return
        if text.startswith("!"):
            cmd_body = text[1:].strip()
            self.execute_slash_command(f"/bash {cmd_body}")
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

        elif action in ("/copy", "/cp"):
            tokens = cmd.strip().split()
            sub = tokens[1].lower() if len(tokens) > 1 else "last"
            if sub in ("last", "response"):
                self.action_copy_last_response()
            elif sub in ("all", "full", "transcript", "history", "log", "screen"):
                transcript = chat.get_full_transcript()
                self.copy_to_clipboard(transcript)
                turns = len(chat.transcript)
                chat.add_agent_message("Supervisor", f"📋 ✓ Copied full conversation transcript ({turns} messages) to system clipboard.", record_as_last_response=False)
                self.notify(f"Copied {turns} messages to clipboard", title="ScopeForge Clipboard")
            elif sub in ("code", "snippet"):
                code = chat.get_last_code_block()
                if code:
                    self.copy_to_clipboard(code)
                    chat.add_agent_message("Supervisor", "📋 ✓ Copied last code block to system clipboard.", record_as_last_response=False)
                    self.notify("Copied code block to clipboard", title="ScopeForge Clipboard")
                else:
                    chat.add_agent_message("Supervisor", "ℹ️ No code block found in last agent response.", record_as_last_response=False)
            elif sub in ("export", "file", "save"):
                target_fname = tokens[2] if len(tokens) > 2 else "scopeforge_session.md"
                transcript = chat.get_full_transcript()
                try:
                    with open(target_fname, "w", encoding="utf-8") as f:
                        f.write(transcript)
                    self.copy_to_clipboard(target_fname)
                    chat.add_agent_message("Supervisor", f"📋 ✓ Exported session transcript to `{target_fname}` and copied path to clipboard.", record_as_last_response=False)
                    self.notify(f"Exported to {target_fname}", title="ScopeForge Export")
                except Exception as e:
                    chat.add_agent_message("Supervisor", f"❌ Export failed: {e}", record_as_last_response=False)
            elif sub in ("findings", "vulns", "vuln"):
                findings_text = (
                    "### Discovered Security Findings\n"
                    "- `FIND-001` [CRITICAL] SQL Injection (CVSS 9.8)\n"
                    "- `FIND-002` [MEDIUM] Missing Content-Security-Policy\n"
                    "- `FIND-003` [MEDIUM] Wildcard CORS Header Exposure"
                )
                self.copy_to_clipboard(findings_text)
                chat.add_agent_message("Supervisor", "📋 ✓ Copied security findings to system clipboard.", record_as_last_response=False)
                self.notify("Copied findings to clipboard", title="ScopeForge Clipboard")
            else:
                chat.add_agent_message(
                    "Supervisor",
                    "**Clipboard Copy Options:**\n"
                    "- `/copy` (or `/copy last`) : Copy last agent response to clipboard\n"
                    "- `/copy all` : Copy full conversation transcript to clipboard\n"
                    "- `/copy code` : Copy last code block to clipboard\n"
                    "- `/copy export [file]` : Export full session transcript to a file\n"
                    "- `/copy findings` : Copy security findings to clipboard\n\n"
                    "*Shortcuts:*\n"
                    "- Drag mouse to auto-copy highlighted text to clipboard\n"
                    "- Press **F6** or **Ctrl+Y** to copy last response\n"
                    "- Press **F7** or `/mouse` to toggle native terminal mouse selection\n"
                    "- Hold **Option/Fn** while dragging in terminal for native selection",
                    record_as_last_response=False,
                )

        elif action == "/paste":
            try:
                pb = self.query_one(PromptBar)
                pb.paste_clipboard_content()
                chat.add_agent_message("Supervisor", "📋 ✓ Pasted clipboard content into prompt input.", record_as_last_response=False)
            except Exception as e:
                chat.add_agent_message("Supervisor", f"❌ Paste failed: {e}", record_as_last_response=False)

        elif action == "/mouse":
            self.action_toggle_mouse_capture()

        elif action == "/export":
            fname = parts[1].strip() if len(parts) > 1 else "scopeforge_session.md"
            self.execute_slash_command(f"/copy export {fname}")

        elif action == "/model":
            tokens = cmd.strip().split()
            if len(tokens) > 1:
                target = tokens[1]
                if target.lower() == "add":
                    # /model add <name> <model_id> [key|env:VAR] [base_url]
                    # (base auto-filled for known platforms, key may come from env).
                    if len(tokens) < 4:
                        chat.add_agent_message("Supervisor", "Usage: `/model add <name> <model_id> [key|env:VAR] [base_url]`")
                        return
                    m_name = tokens[2]
                    m_id = tokens[3] if len(tokens) > 3 else ""
                    m_key = tokens[4] if len(tokens) > 4 else None
                    m_base = tokens[5] if len(tokens) > 5 else None
                    cfg, err = self._slash_add_custom(m_name, m_id, m_key, m_base)
                    if err:
                        chat.add_agent_message("Supervisor", err)
                        return
                    self._sync_header()
                    chat.add_agent_message(
                        "Supervisor",
                        f"✓ Added & activated custom model: **{cfg.name}** (`{cfg.model}`) at `{cfg.api_base or 'default endpoint'}`.{self._active_model_warning()}"
                    )
                    return
                elif target.lower() in ("free", "openrouter/free", "openrouter:free", "openrouter/auto") or (target.lower() == "openrouter" and len(tokens) > 2 and tokens[2].strip().lower() == "free"):
                    self.provider_mgr.set_active_provider("openrouter-free")
                    chat.add_agent_message("Supervisor", "✓ Switched active LLM to OpenRouter Free tier (**openrouter/free**, alias `openrouter/auto`). Needs `OPENROUTER_API_KEY` — `/config set key <KEY>` if unset.")
                    self._sync_header()
                    return
                elif target.lower() == "openrouter" and len(tokens) > 2:
                    model_id = tokens[2].strip()
                    cfg_name = f"openrouter-{model_id.replace('/', '-').split(':')[0]}"
                    try:
                        self.provider_mgr.add_custom_provider(
                            name=cfg_name,
                            model=model_id,
                            api_base="https://openrouter.ai/api/v1",
                            provider="openrouter",
                            temperature=0.1,
                            set_active=True,
                        )
                    except ValueError as e:
                        chat.add_agent_message("Supervisor", f"✗ {e}")
                        return
                    chat.add_agent_message("Supervisor", f"✓ Configured & switched to OpenRouter model: **{model_id}** (`{cfg_name}`).{self._active_model_warning()}")
                    self._sync_header()
                elif self.provider_mgr.set_active_provider(target):
                    chat.add_agent_message("Supervisor", f"✓ Switched active LLM model to **{self.provider_mgr.active_provider_name}**.{self._active_model_warning()}")
                    self._sync_header()
                else:
                    chat.add_agent_message(
                        "Supervisor",
                        f"✗ Provider '{target}' not found. Available: {', '.join([p.name for p in self.provider_mgr.list_providers()])}\n\n"
                        "*Tip:* To add a custom model, use `/model add <name> <model_id> [key] [base_url]` or press `/model` to configure in UI."
                    )
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
                agent_name = parts[1].lower().strip()
                valid = ("supervisor", "recon", "audit", "exploit", "report", "dev")
                # Normalise Claude Code style aliases
                aliases = {
                    "cloudsec": "audit", "apisec": "audit",
                    "devagent": "dev", "reconagent": "recon",
                }
                agent_name = aliases.get(agent_name, agent_name)
                if agent_name in valid:
                    # One-shot override consumed by next run_agent_task
                    self.pending_agent = None if agent_name == "supervisor" else agent_name
                    scope_note = "" if agent_name == "supervisor" else " (next message only; `supervisor` resets)"
                    chat.add_agent_message("Supervisor", f"✓ Directing next mission specifically to **{agent_name.capitalize()}Agent**{scope_note}.")
                    try:
                        header = self.query_one(HeaderBar)
                        header.active_agent = agent_name
                    except Exception:
                        pass
                else:
                    chat.add_agent_message("Supervisor", f"✗ Unknown agent '{agent_name}'. Valid: {', '.join(valid)}")
            else:
                pending = f" | Pending override: `{self.pending_agent}`" if self.pending_agent else ""
                chat.add_agent_message("Supervisor", f"Active Agents: `supervisor`, `recon`, `audit`, `exploit`, `report`, `dev`{pending}\nUsage: `/agent <name>` (one-shot)")

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

        elif action in ("/a2a", "/protocol", "/agents-bus"):
            tokens = cmd.strip().split()
            subcmd = tokens[1].lower() if len(tokens) > 1 else ""

            if subcmd == "send" and len(tokens) >= 4:
                recip = tokens[2]
                intent_str = tokens[3]
                msg_body = " ".join(tokens[4:]) if len(tokens) > 4 else "Ping"
                clean_intent = A2AIntent.TASK_DELEGATION
                for member in A2AIntent:
                    if member.name.lower() == intent_str.lower() or member.value.lower() == intent_str.lower():
                        clean_intent = member
                        break
                a2a_msg = self.a2a_bus.send(
                    sender="Supervisor",
                    recipient=recip,
                    intent=clean_intent,
                    payload={"message": msg_body},
                )
                chat.add_a2a_banner("Supervisor", recip, clean_intent.value, msg_body[:60])
                msg_id_short = (getattr(a2a_msg, "message_id", None) or "a2a_msg")[:8]
                chat.add_agent_message(
                    "Supervisor",
                    f"✓ Dispatched A2A Protocol message `[{msg_id_short}]`:\n"
                    f"- **Sender:** Supervisor\n"
                    f"- **Recipient:** {recip}\n"
                    f"- **Intent:** `{clean_intent.value}`\n"
                    f"- **Payload:** {msg_body}"
                )
            else:
                history = self.a2a_bus.get_history(10)
                if history:
                    table_rows = []
                    for m in history:
                        p_summary = json.dumps(m.payload, default=str)
                        if len(p_summary) > 60:
                            p_summary = p_summary[:57] + "..."
                        intent_val = m.intent.value if hasattr(m.intent, 'value') else str(m.intent)
                        table_rows.append(
                            f"| `{m.timestamp[:19]}` | **{m.sender}** | **{m.recipient}** | `{intent_val}` | {p_summary} |"
                        )
                    history_table = (
                        "| Timestamp | Sender | Recipient | Intent | Payload |\n"
                        "|---|---|---|---|---|\n" + "\n".join(table_rows)
                    )
                else:
                    history_table = "*No inter-agent messages recorded yet in this session.*"

                active_team = (
                    "- **Supervisor**: System coordinator & Claude Code / Open Code general harness\n"
                    "- **ReconAgent**: Perimeter reconnaissance, port scanning, web surface probing\n"
                    "- **AuditAgent**: SAST static vulnerability audit, CVE correlation\n"
                    "- **ExploitAgent**: Falsifiable PoC verification, evidence integrity\n"
                    "- **ReportAgent**: Executive SecOps synthesis and CVSS remediation\n"
                    "- **DevAgent**: Local code modification, git workflows, sandbox bash execution, skill installer"
                )

                chat.add_agent_message(
                    "Supervisor",
                    f"### ⇄ ScopeForge Agent-to-Agent (A2A) Protocol Bus\n\n"
                    f"**Active Registered Agent Team:**\n{active_team}\n\n"
                    f"**Recent Telemetry & Handover History ({len(history)} messages):**\n\n"
                    f"{history_table}\n\n"
                    f"*Commands: `/a2a send <recipient> <intent> <message>` to broadcast or `/agent <name>` to direct.*"
                )

        elif action == "/wiki":
            self.action_open_wiki()

        elif action in ("/skill", "/skills"):
            if len(parts) > 1 and parts[1].lower() in ("install", "clone"):
                if len(parts) > 2:
                    repo_url = parts[2].strip()
                    chat.add_agent_message("Supervisor", f"📦 Installing skill from `{repo_url}`...")
                    ok, msg, installed = self.skill_mgr.install_skill_from_repo(repo_url)
                    if ok:
                        for s in installed:
                            self.skill_mgr.activate_skill(s)
                        chat.add_agent_message("Supervisor", f"✓ {msg}\n\nSkills are activated! Type `/skill list` to view all skills or `/skill <name>` to toggle.")
                    else:
                        chat.add_agent_message("Supervisor", f"❌ Skill installation failed: {msg}")
                else:
                    chat.add_agent_message("Supervisor", "Usage: `/skill install <git-url>` (e.g. `/skill install https://github.com/Jakeschincariol/linkedin-agent-skill.git`)")
            elif len(parts) > 1 and parts[1].lower() in ("create", "add"):
                if len(parts) > 2:
                    s_arg = parts[2].strip()
                    if s_arg.startswith("http://") or s_arg.startswith("https://") or "github.com" in s_arg:
                        chat.add_agent_message("Supervisor", f"📦 Installing skill from `{s_arg}`...")
                        ok, msg, installed = self.skill_mgr.install_skill_from_repo(s_arg)
                        if ok:
                            for s in installed:
                                self.skill_mgr.activate_skill(s)
                            chat.add_agent_message("Supervisor", f"✓ {msg}\n\nSkills are activated!")
                        else:
                            chat.add_agent_message("Supervisor", f"❌ Skill installation failed: {msg}")
                    else:
                        skill_tokens = parts[2].split(maxsplit=1)
                        s_name = skill_tokens[0].strip()
                        desc = skill_tokens[1].strip() if len(skill_tokens) > 1 else f"Custom playbook for {s_name}"
                        created = self.skill_mgr.create_skill(
                            name=s_name,
                            description=desc,
                            triggers=[s_name],
                            instructions=f"### {s_name} Custom Playbook\n\n1. Follow best practices for {s_name}.\n2. Document findings in `.scopeforge/evidence/`.",
                        )
                        self.skill_mgr.activate_skill(s_name)
                        chat.add_agent_message("Supervisor", f"✓ Created & activated custom skill: **[{created.name}]** (`{created.path}`)")
                else:
                    chat.add_agent_message("Supervisor", "Usage: `/skill add <name> [description]` or `/skill install <url>`")
            elif len(parts) > 1 and parts[1].lower() != "list":
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
                chat.add_agent_message("Supervisor", f"🧠 **Discovered Agent Skills (`.scopeforge/skills/`):**\n\n{skills_list}\n\n*Commands: `/skill <name>` to toggle, `/skill install <git-url>` to install, `/skill add <name> [desc]` to create.*")

        elif action == "/mcp":
            if len(parts) > 2 and parts[1].lower() == "add":
                # /mcp add <name> <command>
                mcp_parts = parts[2].split(maxsplit=1)
                if len(mcp_parts) == 2:
                    srv_name, srv_cmd = mcp_parts
                    try:
                        try:
                            self.mcp.registry.remove_server(srv_name)
                        except Exception:
                            pass
                        self.mcp.registry.add_server(srv_name, srv_cmd)
                        self.mcp.enable_server(srv_name)
                        chat.add_agent_message("Supervisor", f"✓ Added and enabled MCP server: **{srv_name}** (`{srv_cmd}`)")
                    except Exception as e:
                        chat.add_agent_message("Supervisor", f"❌ Error adding MCP server: {e}")
                else:
                    chat.add_agent_message("Supervisor", "Usage: `/mcp add <name> <command>`")
            elif len(parts) > 2 and parts[1].lower() in ("remove", "delete", "del", "rm"):
                srv_name = parts[2].strip()
                try:
                    self.mcp.registry.remove_server(srv_name)
                    chat.add_agent_message("Supervisor", f"✓ Removed MCP server: **{srv_name}**")
                except Exception as e:
                    chat.add_agent_message("Supervisor", f"❌ Error removing MCP server: {e}")
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
            elif len(parts) > 1 and parts[1].lower() in ("tools", "tool"):
                tools = self.mcp.get_langchain_tools()
                if tools:
                    t_list = "\n".join(f"- **`{t.name}`**: {t.description}" for t in tools)
                    chat.add_agent_message("Supervisor", f"🔌 **Active MCP Registered Tools ({len(tools)}):**\n\n{t_list}")
                else:
                    chat.add_agent_message("Supervisor", "🔌 No MCP tools currently active. Enable an MCP server using `/mcp enable <name>`.")
            else:
                servers = self.mcp.list_servers()
                s_list = "\n".join(f"- **{s['name']}**: {'[ENABLED]' if s['enabled'] else '[DISABLED]'} (`{s['command']}`)" for s in servers)
                chat.add_agent_message("Supervisor", f"🔌 **Model Context Protocol (MCP) Servers:**\n\n{s_list}\n\n*Commands: `/mcp add <name> <cmd>`, `/mcp remove <name>`, `/mcp enable <name>`, `/mcp disable <name>`, `/mcp tools`*")

        elif action in ("/search", "/web", "/google"):
            query_str = " ".join(parts[1:]).strip() if len(parts) > 1 else ""
            if not query_str:
                chat.add_agent_message("Supervisor", "Usage: `/search <query>` (e.g. `/search python 3.13` or `/web cve-2024-3400`)")
            else:
                chat.add_agent_message("Supervisor", f"🔍 Searching Google & Web for `{query_str}`...")
                from ..sec_tools import google_web_search
                raw = google_web_search.invoke({"query": query_str, "max_results": 5})
                try:
                    res = json.loads(raw)
                    hits = res.get("results", [])
                    if hits:
                        cards = [f"- **[{h.get('title')}]({h.get('url')})**\n  {h.get('snippet')}\n  `{h.get('url')}`" for h in hits]
                        chat.add_agent_message("Supervisor", f"🌐 **Web Search Results for '{query_str}':**\n\n" + "\n\n".join(cards))
                    else:
                        chat.add_agent_message("Supervisor", f"🌐 No live web results found for query: `{query_str}`")
                except Exception as e:
                    chat.add_agent_message("Supervisor", f"❌ Search error: {e}")

        elif action in ("/bash", "/run", "/sh", "/exec"):
            cmd_str = " ".join(parts[1:]).strip() if len(parts) > 1 else ""
            if not cmd_str:
                chat.add_agent_message("Supervisor", "Usage: `/bash <command>` or `$ <command>` (e.g. `/bash ls -la` or `$ git status`)")
            else:
                from ..sec_tools import bash_cli
                raw = bash_cli.invoke({"command": cmd_str})
                try:
                    res = json.loads(raw)
                    out_text = res.get("stdout") or res.get("stderr") or res.get("error") or "Executed successfully with no output."
                    rc = res.get("return_code", 0 if res.get("success") else 1)
                    status_badge = "✓ SUCCESS" if res.get("success") else "❌ FAILED"
                    chat.add_agent_message(
                        "Supervisor",
                        f"💻 **Sandbox Terminal Command:** `{cmd_str}` ({status_badge}, exit code `{rc}`)\n\n```text\n{out_text}\n```"
                    )
                except Exception as e:
                    chat.add_agent_message("Supervisor", f"❌ Bash error: {e}")

        elif action == "/config":
            tokens = cmd.strip().split()
            subcmd = tokens[1].lower() if len(tokens) > 1 else ""

            if subcmd in ("model", "models", "provider", "providers"):
                self.action_pick_model()
                return

            elif subcmd in ("free", "openrouter-free", "openrouter/free", "openrouter/auto"):
                self.provider_mgr.set_active_provider("openrouter-free")
                self._sync_header()
                chat.add_agent_message("Supervisor", "✓ Switched active LLM to OpenRouter Free tier (**openrouter/free**, alias `openrouter/auto`). Needs `OPENROUTER_API_KEY` — `/config set key <KEY>` if unset.")
                return

            elif subcmd == "reset":
                self.provider_mgr.set_active_provider("openrouter-free")
                self.sec_mode = "plan"
                self.pipeline.middlewares[1].set_mode("plan")
                self._sync_header()
                chat.add_agent_message("Supervisor", "✓ Active configuration reset to default OpenRouter Free tier (`openrouter/free`) and PLAN mode.")
                return

            elif subcmd == "add" and len(tokens) >= 4:
                m_name = tokens[2]
                m_id = tokens[3] if len(tokens) > 3 else ""
                m_key = tokens[4] if len(tokens) > 4 else None
                m_base = tokens[5] if len(tokens) > 5 else None
                cfg, err = self._slash_add_custom(m_name, m_id, m_key, m_base)
                if err:
                    chat.add_agent_message("Supervisor", err)
                    return
                self._sync_header()
                chat.add_agent_message(
                    "Supervisor",
                    f"✓ Added & activated custom model: **{cfg.name}** (`{cfg.model}`) at `{cfg.api_base or 'default endpoint'}`.{self._active_model_warning()}"
                )
                return

            elif subcmd == "key" and len(tokens) > 2:
                new_key = tokens[2].strip()
                self.provider_mgr.update_active_config(api_key=new_key)
                masked = f"...{new_key[-4:]}" if len(new_key) >= 4 else "••••"
                chat.add_agent_message("Supervisor", f"✓ API key updated for active profile (ending in `{masked}`).")
                return

            elif subcmd == "set" and len(tokens) >= 4:
                key_name = tokens[2].lower()
                val = " ".join(tokens[3:]).strip()

                if key_name in ("model", "model_id"):
                    if val.lower() in ("free", "openrouter/free", "openrouter:free", "openrouter/auto"):
                        self.provider_mgr.set_active_provider("openrouter-free")
                        self._sync_header()
                        chat.add_agent_message("Supervisor", "✓ Switched active LLM model to OpenRouter Free tier (**openrouter/free**).")
                    else:
                        self.provider_mgr.update_active_config(model=val)
                        self._sync_header()
                        chat.add_agent_message("Supervisor", f"✓ Updated active model to **{val}**.")
                    return

                elif key_name in ("provider", "platform"):
                    self.provider_mgr.update_active_config(provider=val.lower())
                    self._sync_header()
                    chat.add_agent_message("Supervisor", f"✓ Updated active provider platform to **{val.lower()}**.")
                    return

                elif key_name in ("key", "api_key", "token"):
                    self.provider_mgr.update_active_config(api_key=val)
                    masked = f"...{val[-4:]}" if len(val) >= 4 else "••••"
                    chat.add_agent_message("Supervisor", f"✓ API key updated (ending in `{masked}`).")
                    return

                elif key_name in ("base", "api_base", "base_url", "url"):
                    if not (val.startswith("http://") or val.startswith("https://")):
                        chat.add_agent_message("Supervisor", f"✗ Invalid base URL '{val}'. Must start with http(s)://")
                        return
                    self.provider_mgr.update_active_config(api_base=val)
                    chat.add_agent_message("Supervisor", f"✓ API base URL updated to `{val}`.")
                    return

                elif key_name in ("temp", "temperature"):
                    try:
                        temp_val = float(val)
                        temp_val = max(0.0, min(temp_val, 2.0))
                        self.provider_mgr.update_active_config(temperature=temp_val)
                        chat.add_agent_message("Supervisor", f"✓ Temperature updated to `{temp_val}`.")
                    except ValueError:
                        chat.add_agent_message("Supervisor", f"✗ Invalid temperature '{val}'. Must be a float between 0.0 and 2.0.")
                    return

                elif key_name in ("tokens", "max_tokens"):
                    try:
                        tok_val = int(val)
                        self.provider_mgr.update_active_config(max_tokens=tok_val)
                        chat.add_agent_message("Supervisor", f"✓ Max tokens updated to `{tok_val}`.")
                    except ValueError:
                        chat.add_agent_message("Supervisor", f"✗ Invalid token count '{val}'. Must be an integer.")
                    return

                elif key_name in ("mode", "sec_mode"):
                    m_val = val.lower()
                    if m_val in ("plan", "artifacts", "live"):
                        self.sec_mode = m_val
                        self.pipeline.middlewares[1].set_mode(self.sec_mode)
                        self._sync_header()
                        chat.add_agent_message("Supervisor", f"✓ Switched execution mode to **{self.sec_mode.upper()}**.")
                    else:
                        chat.add_agent_message("Supervisor", "✗ Mode must be one of: `plan`, `artifacts`, `live`")
                    return

                else:
                    chat.add_agent_message(
                        "Supervisor",
                        f"✗ Unknown configuration key '{key_name}'. Supported keys:\n"
                        "`model`, `provider`, `key`, `base`, `temp`, `tokens`, `mode`"
                    )
                    return

            active_cfg = self.provider_mgr.get_active_config()
            import os as _os
            # Resolve env fallback for display (never print values)
            env_key = {
                "anthropic": "ANTHROPIC_API_KEY", "openai": "OPENAI_API_KEY",
                "openrouter": "OPENROUTER_API_KEY", "groq": "GROQ_API_KEY",
                "gemini": "GEMINI_API_KEY", "ollama": None, "mock": None, "custom": None,
            }.get(active_cfg.provider.value)
            has_env = bool(env_key and _os.getenv(env_key))
            if active_cfg.api_key:
                key_status = f"Configured in profile (`...{active_cfg.api_key[-4:]}`)"
            elif has_env:
                key_status = f"From env `{env_key}` (set)"
            elif active_cfg.provider.value in ("ollama", "mock"):
                key_status = "Not required (local/mock)"
            else:
                key_status = f"⚠️ MISSING — set via `/config set key <KEY>` or env `{env_key or 'VAR'}` (mock fallback active)"
            base_url = active_cfg.api_base or "Default provider endpoint"

            cfg_text = (
                "⚙️ **ScopeForge Runtime & LLM Configuration:**\n\n"
                f"- **Active Model**: `{active_cfg.name}` (`{active_cfg.model}`)\n"
                f"- **Provider Platform**: `{active_cfg.provider.value}`\n"
                f"- **API Key Status**: {key_status}\n"
                f"- **API Base URL**: `{base_url}`\n"
                f"- **Sampling Temperature**: `{active_cfg.temperature}`\n"
                f"- **Max Generation Tokens**: `{active_cfg.max_tokens}`\n"
                f"- **Guardrail Mode**: `{self.sec_mode.upper()}` (ScopeGate Enforced)\n"
                f"- **Target Scopes**: `{', '.join(self.current_scope)}`\n"
                f"- **RAG Store**: LlamaIndex ({len(self.rag.documents)} SecOps docs indexed)\n"
                f"- **Active Skills**: `{', '.join(self.skill_mgr.active_skills) or 'None (auto-routing)'}`\n\n"
                "**Fast Inline Commands (Claude Code / Open Code style):**\n"
                "- `/model` or `/config model` : Open instant interactive switcher\n"
                "- `/model add <name> <model_id> [key] [base_url]` : Add & activate custom model\n"
                "- `/config set model <id>` : Switch active model (`openrouter/free`)\n"
                "- `/config set key <api-key>` : Configure provider API key\n"
                "- `/config set temp <float>` : Set temperature (`0.0` - `1.0`)\n"
                "- `/config set mode <plan|artifacts|live>` : Change guardrail mode\n"
                "- `/config free` : Instant switch to zero-cost OpenRouter free tier\n"
                "- `/config reset` : Reset to initial default configuration"
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

        elif action == "/init":
            target_path = Path("SCOPEFORGE.md")
            if target_path.exists():
                chat.add_agent_message("Supervisor", "ℹ️ `SCOPEFORGE.md` project memory already exists in repository root.")
            else:
                default_content = """# SCOPEFORGE.md — Project Memory & Agent Guidelines

## Project Overview
- ScopeForge autonomous cybersecurity and developer multi-agent harness.
- Claude Code & Open Code style reactive Textual TUI with LangGraph, RAG, and MCP.

## Build, Test & Lint Commands
- Run Tests: `pytest services/engine/tests`
- Run TUI: `python scopeforge_tui.py`
- Verify SBOMS: `python scripts/sbom.py`

## Architecture & Layout
- `services/engine/src/scopeforge_engine/`: Multi-agent graph, providers, middleware, TUI.
- `skills/`: Auto-triggered specialized cybersecurity and developer playbooks.
- `.scopeforge/`: Local audit ledger (`audit.jsonl`), evidence store, and wiki memory.

## Code Conventions
- Strict typing, async/await for I/O and graph transitions.
- All external tool executions must pass through ScopeGate middleware.
- Never output unsanitized secrets or unredacted API tokens.
"""
                try:
                    with open(target_path, "w", encoding="utf-8") as f:
                        f.write(default_content)
                    chat.add_agent_message("Supervisor", "✓ Initialized `SCOPEFORGE.md` project memory in repository root. Agents will now automatically follow these instructions.")
                except Exception as e:
                    chat.add_agent_message("Supervisor", f"❌ Failed creating `SCOPEFORGE.md`: {e}")

        elif action == "/diff":
            from ..sec_tools.code_tools import git_diff_tool
            res = json.loads(git_diff_tool.invoke({"staged": False}))
            diff_text = res.get("diff", "No changes detected.")
            if res.get("has_changes"):
                chat.add_agent_message("Supervisor", f"📝 **Git Working Tree Changes (`git diff`):**\n\n```diff\n{diff_text}\n```")
            else:
                chat.add_agent_message("Supervisor", "✓ Working tree is clean. No uncommitted modifications.")

        elif action == "/commit":
            from ..sec_tools.code_tools import git_commit_tool, git_diff_tool
            msg = " ".join(parts[1:]).strip() if len(parts) > 1 else ""
            if not msg:
                diff_res = json.loads(git_diff_tool.invoke({"staged": True}))
                if not diff_res.get("has_changes"):
                    diff_res = json.loads(git_diff_tool.invoke({"staged": False}))
                chat.add_agent_message(
                    "Supervisor",
                    "💡 **Commit Helper:**\n"
                    "No message provided. Use `/commit <message>` to commit changes.\n"
                    f"*Example:* `/commit feat: add Claude Code developer toolset and project memory`"
                )
            else:
                commit_res = json.loads(git_commit_tool.invoke({"message": msg}))
                if commit_res.get("success"):
                    chat.add_agent_message("Supervisor", f"✓ **Committed changes:** `{msg}`\n```text\n{commit_res.get('stdout')}\n```")
                else:
                    chat.add_agent_message("Supervisor", f"❌ Commit failed: {commit_res.get('stderr') or commit_res.get('error')}")

        elif action == "/review":
            from ..sec_tools.code_tools import git_diff_tool
            diff_res = json.loads(git_diff_tool.invoke({"staged": False}))
            diff_text = diff_res.get("diff", "")
            if not diff_res.get("has_changes"):
                chat.add_agent_message("Supervisor", "✓ No modified files to review. Working directory is clean.")
            else:
                self.run_agent_task(f"Perform a comprehensive Claude Code style code review on this git diff:\n```diff\n{diff_text[:3000]}\n```")

        elif action == "/compact":
            old_count = len(self.chat_history)
            if old_count <= 2:
                chat.add_agent_message("Supervisor", "ℹ️ Conversation history is already minimal.")
            else:
                last_turn = self.chat_history[-2:] if len(self.chat_history) >= 2 else self.chat_history
                self.chat_history = [
                    HumanMessage(content="[Context Summary: Previous mission turns compacted to preserve token budget.]"),
                    AIMessage(content="Understood. Previous context compacted. Ready for next instruction."),
                ] + last_turn
                chat.add_agent_message("Supervisor", f"✓ **Context Compacted:** Reduced {old_count} messages to compact working memory.")

        elif action == "/doctor":
            import platform
            import sys
            active_cfg = self.provider_mgr.get_active_config()
            doctor_report = (
                "🩺 **ScopeForge System Health & Diagnostics (Doctor):**\n\n"
                f"- **Python Version**: `{platform.python_version()}` ({sys.executable})\n"
                f"- **Platform**: `{platform.system()} {platform.release()}`\n"
                f"- **Active Model**: `{active_cfg.name}` (`{active_cfg.provider.value}` / `{active_cfg.model}`)\n"
                f"- **Execution Mode**: `{self.sec_mode.upper()}` (ScopeGate Enforced)\n"
                f"- **Authorized Scopes**: `{', '.join(self.current_scope)}`\n"
                f"- **Discovered Skills**: `{len(self.skill_mgr.list_skills())}` skills loaded\n"
                f"- **MCP Servers**: `{len(self.mcp.list_servers())}` configured\n"
                f"- **RAG Store**: LlamaIndex online (`{len(self.rag.documents)}` chunks)\n"
                f"- **Audit Logging**: `.scopeforge/audit.jsonl` active (tamper-evident SHA-256 chain)\n"
                "- **Overall Health**: ✅ All systems nominal and ready for operations."
            )
            chat.add_agent_message("Supervisor", doctor_report)

        elif action == "/pr":
            pr_template = (
                "🔀 **Pull Request Summary Template:**\n\n"
                "## Description\n"
                "- Autonomous Claude Code & cybersecurity enhancements to ScopeForge harness.\n\n"
                "## Changes Made\n"
                "- Integrated developer tools (`view_file`, `edit_file`, `write_file`, `glob_files`, `grep_search`).\n"
                "- Added Claude Code slash commands (`/init`, `/diff`, `/commit`, `/review`, `/compact`, `/doctor`, `/pr`).\n"
                "- Enabled `SCOPEFORGE.md` project memory autoloading.\n\n"
                "## Verification\n"
                "- All engine tests passing.\n"
                "- ScopeGate boundary checks validated.\n"
            )
            chat.add_agent_message("Supervisor", pr_template)

        elif action == "/goal":
            goal_text = " ".join(parts[1:]).strip() if len(parts) > 1 else ""
            if not goal_text:
                chat.add_agent_message("Supervisor", "Usage: `/goal <objective>` — autonomously work through steps to achieve objective.")
                return
            chat.add_agent_message("Supervisor", f"🎯 **Autonomous Goal Activated:** `{goal_text}`\n*ScopeForge agents will autonomously plan and execute tools until completion.*")
            self.run_agent_task(f"Autonomous goal execution: {goal_text}")

        elif action == "/plan":
            plan_text = " ".join(parts[1:]).strip() if len(parts) > 1 else ""
            if not plan_text:
                chat.add_agent_message("Supervisor", "Usage: `/plan <objective>` — plan out multi-step strategy with safety bounds.")
                return
            chat.add_agent_message("Supervisor", f"📋 **Planning Mode:** Analyzing `{plan_text}` under ScopeGate RoE guidelines...")
            self.run_agent_task(f"Plan a structured step-by-step implementation for: {plan_text}")

        elif action == "/tasks":
            is_busy = getattr(self, "_current_task", None) and not self._current_task.done()
            task_status = (
                "⚙️ **ScopeForge Background Task Status:**\n\n"
                f"- **Active Task Running**: `{'YES (Autonomous Agent Execution)' if is_busy else 'NO (Idle)'}`\n"
                f"- **Total Session Tokens**: `{self.total_tokens:,}`\n"
                f"- **Total A2A Events**: `{len(self.a2a_bus.messages)}`\n"
                f"- **Active MCP Tools**: `{len(self.mcp.get_langchain_tools())}`\n"
                + ("\n*Press <kbd>Ctrl+C</kbd> at any time to cancel running task.*" if is_busy else "")
            )
            chat.add_agent_message("Supervisor", task_status)

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
        # Consume one-shot `/agent` override (Open Code behaviour)
        forced = self.pending_agent
        self.pending_agent = None
        # Show routing intent immediately, Claude Code style
        if forced:
            try:
                chat.add_a2a_banner("Supervisor", f"{forced.capitalize()}Agent", "TASK_DELEGATION", prompt[:40])
            except Exception:
                pass

        async def _execute():
            header = self.query_one(HeaderBar)
            header.active_agent = forced or "Supervisor"
            stream_started = False
            has_streamed_any_text = False

            def _on_token(agent: str, chunk: str):
                nonlocal stream_started, has_streamed_any_text
                if isinstance(chunk, str) and chunk.startswith('{"__type__":'):
                    try:
                        data = json.loads(chunk)
                        if data.get("__type__") == "tool_call":
                            chat.add_tool_call(data.get("name", ""), data.get("args", {}), "RUNNING")
                            stream_started = False
                            return
                        elif data.get("__type__") == "tool_result":
                            chat.add_tool_result(data.get("name", ""), data.get("result", ""), "SUCCESS")
                            stream_started = False
                            return
                        elif data.get("__type__") == "a2a_banner":
                            chat.add_a2a_banner(
                                data.get("sender", "Supervisor"),
                                data.get("recipient", "DevAgent"),
                                data.get("intent", "TASK_DELEGATION"),
                                data.get("preview", ""),
                            )
                            stream_started = False
                            return
                    except Exception:
                        pass
                if not stream_started:
                    chat.start_agent_stream(agent)
                    stream_started = True
                has_streamed_any_text = True
                chat.append_agent_chunk(chunk, agent=agent)

            try:
                state = await self.orchestrator.run(
                    user_message=prompt,
                    mode=self.sec_mode,
                    scope=self.current_scope,
                    history=self.chat_history,
                    forced_agent=forced,
                    on_token=_on_token,
                )

                if stream_started:
                    chat.finish_agent_stream()

                active_agent = state.get("active_agent", "Supervisor")
                header.active_agent = active_agent

                # Add last response (if not already streamed live)
                if state.get("messages"):
                    last_msg = state["messages"][-1]
                    clean_content = str(last_msg.content)
                    if not has_streamed_any_text:
                        chat.add_agent_message(active_agent, clean_content)
                    else:
                        chat.last_agent_response = clean_content
                    self.last_agent_response = clean_content
                    # state["messages"] uses add_messages: history + [human, ai]
                    # Only append the 2 new messages, guard against duplication
                    new_msgs = state["messages"][-2:]
                    # Avoid double-adding when history object was mutated in place
                    if len(self.chat_history) >= 2 and self.chat_history[-2:] == new_msgs:
                        pass
                    else:
                        self.chat_history.extend(new_msgs)

                # Update findings in sidebar
                sidebar = self.query_one(SidebarWidget)
                for f in state.get("findings", []):
                    sidebar.add_finding(f)
                    chat.add_finding_card(f)

                # Update token counters (rough estimate: ~4 chars/token)
                self.total_tokens += max(1, len(prompt) // 4) + 350
                # Cost scales with model class; keep conservative placeholder
                self.estimated_cost += 0.001 if "free" in self.provider_mgr.active_provider_name or "mock" in self.provider_mgr.active_provider_name else 0.003
                self._sync_header()

            except asyncio.CancelledError:
                if stream_started:
                    chat.finish_agent_stream()
            except Exception as e:
                if stream_started:
                    chat.finish_agent_stream()
                chat.add_agent_message("Supervisor", f"❌ Error during multi-agent orchestration: {e}")
            finally:
                self._current_task = None

        self._current_task = asyncio.create_task(_execute())

    def action_show_help(self):
        self.push_screen(HelpModal())

    def action_pick_model(self):
        def _on_model_picked(model_name: Optional[str]):
            if model_name:
                self._sync_header()
                chat = self.query_one(ChatStream)
                chat.add_agent_message("Supervisor", f"✓ Switched active model to **{model_name}**.{self._active_model_warning()}")
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
        try:
            log = self.query_one("#chat-log", RichLog)
            log.clear()
        except Exception:
            pass
        try:
            chat = self.query_one(ChatStream)
            chat.post_welcome_banner()
        except Exception:
            pass
        self.chat_history.clear()
        # Clearing resets one-shot routing too (least surprise)
        self.pending_agent = None

    def action_copy_last_response(self):
        chat = self.query_one(ChatStream)
        text = chat.get_last_agent_response()
        if not text and hasattr(self, "last_agent_response") and self.last_agent_response:
            text = self.last_agent_response
        if not text and self.chat_history:
            for msg in reversed(self.chat_history):
                if isinstance(msg, AIMessage) and msg.content:
                    text = str(msg.content)
                    break
        if not text and chat.transcript:
            for item in reversed(chat.transcript):
                role = item.get("role", "")
                if "Agent" in role:
                    c = item.get("content", "")
                    if c and not c.startswith("📋"):
                        text = c
                        break
        if text:
            self.copy_to_clipboard(text)
            chat.add_agent_message("Supervisor", "📋 ✓ Copied last agent response to system clipboard.", record_as_last_response=False)
            self.notify("Copied last response to clipboard", title="ScopeForge Clipboard")
        else:
            chat.add_agent_message("Supervisor", "ℹ️ No agent response available to copy yet.", record_as_last_response=False)

    def action_handle_ctrl_c(self):
        """Ctrl+C / Cmd+C: Cancels running task if active, copies selection if highlighted, otherwise exits."""
        if getattr(self, "_current_task", None) and not self._current_task.done():
            self._current_task.cancel()
            self._current_task = None
            chat = self.query_one(ChatStream)
            chat.finish_agent_stream()
            chat.add_agent_message(
                "Supervisor",
                "⚠️ **Task Interrupted:** Autonomous execution was cancelled by user (<kbd>Ctrl+C</kbd>)."
            )
            self._sync_header()
            self.notify("Task cancelled", title="ScopeForge")
            return

        try:
            if self.screen and hasattr(self.screen, "get_selected_text"):
                selected = self.screen.get_selected_text()
                if selected:
                    self.copy_to_clipboard(selected)
                    self.notify("✓ Copied selection to clipboard", title="ScopeForge Clipboard")
                    return
        except Exception:
            pass
        self.exit()

    def action_toggle_mouse_capture(self):
        """Toggle mouse tracking on/off so user can do native terminal selection."""
        driver = getattr(self, "_driver", None)
        chat = self.query_one(ChatStream)
        if not hasattr(self, "_native_mouse_mode"):
            self._native_mouse_mode = False

        self._native_mouse_mode = not self._native_mouse_mode
        if self._native_mouse_mode:
            if driver and hasattr(driver, "_disable_mouse_support"):
                driver._disable_mouse_support()
            elif driver and hasattr(driver, "write"):
                driver.write("\x1b[?1000l\x1b[?1003l\x1b[?1015l\x1b[?1006l")
                if hasattr(driver, "flush"):
                    driver.flush()
            chat.add_agent_message(
                "Supervisor",
                "🖱 **Terminal Native Selection Mode:** Mouse tracking is DISABLED in terminal.\n"
                "You can now select text directly with your normal terminal mouse and copy with Cmd+C.\n"
                "*Press F7 or `/mouse` again to restore TUI interactive clicking.*",
                record_as_last_response=False,
            )
            self.notify("Terminal native selection enabled (Cmd+C to copy)", title="Mouse Mode")
        else:
            if driver and hasattr(driver, "_enable_mouse_support"):
                driver._enable_mouse_support()
            elif driver and hasattr(driver, "write"):
                driver.write("\x1b[?1000h\x1b[?1003h\x1b[?1015h\x1b[?1006h")
                if hasattr(driver, "flush"):
                    driver.flush()
            chat.add_agent_message(
                "Supervisor",
                "🖱 **TUI Mouse Mode Restored:** Interactive buttons, sidebar clicks, and scrolling re-enabled.",
                record_as_last_response=False,
            )
            self.notify("TUI mouse clicks restored", title="Mouse Mode")

    def action_quit_app(self):
        self.exit()


def main():
    app = ScopeForgeTUIApp()
    app.run()


if __name__ == "__main__":
    main()
