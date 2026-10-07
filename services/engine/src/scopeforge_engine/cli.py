"""ScopeForge — Claude Code & AGY style Autonomous Cybersecurity CLI & REPL.

Provides a fast, minimalist, interactive terminal interface running in native
terminal scrollback with streaming markdown, dynamic tool execution cards,
inline ScopeGate human-in-the-loop (HITL) authorization, and slash commands.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import platform
import sys
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage
from prompt_toolkit import PromptSession
from prompt_toolkit.auto_suggest import AutoSuggestFromHistory
from prompt_toolkit.completion import Completer, Completion
from prompt_toolkit.formatted_text import HTML
from prompt_toolkit.history import FileHistory
from rich.box import ROUNDED
from rich.console import Console
from rich.markdown import Markdown
from rich.panel import Panel
from rich.syntax import Syntax
from rich.table import Table

from . import __version__
from .a2a.bus import A2ABus
from .a2a.protocol import A2AIntent
from .agents.graph import MultiAgentSecOpsOrchestrator
from .config.loader import ScopeGateConfig
from .llm_providers.manager import ProviderManager
from .mcp_bridge import MCPBridge
from .middleware.base import ToolApprovalRequest
from .middleware.pipeline import MiddlewarePipeline, create_default_pipeline
from .rag.engine import LlamaSecRAG
from .skills.manager import SkillManager
from .wiki.store import SecWiki


class ScopeForgeSlashCompleter(Completer):
    """Fuzzy auto-completer with descriptive metadata for slash commands."""

    COMMANDS = [
        ("/help", "Show all available commands, modes, and shortcuts"),
        ("/mode", "Switch ScopeGate mode (plan, redteam, blueteam, audit, live)"),
        ("/model", "View or switch active LLM provider and model"),
        ("/model add", "Register a custom LLM model (interactive wizard or inline)"),
        ("/config", "View or update provider settings (key, model, base URL)"),
        ("/scope", "Inspect or add authorized target domains/IPs"),
        ("/agent", "Direct next prompt to a specific agent (recon, audit, exploit, dev)"),
        ("/diff", "View git diff of uncommitted workspace modifications"),
        ("/commit", "Commit staged/working workspace changes"),
        ("/review", "Autonomous security code review of git modifications"),
        ("/init", "Initialize SCOPEFORGE.md project memory in repository root"),
        ("/doctor", "Run system diagnostics, environment, and connectivity checks"),
        ("/compact", "Compact conversation context to preserve token budget"),
        ("/cost", "Display session token usage and estimated cost"),
        ("/wiki", "Search or inspect security wiki knowledge memory"),
        ("/rag", "Query LlamaIndex security vector knowledge base"),
        ("/clear", "Clear terminal screen and redraw header"),
        ("/tui", "Launch legacy full-screen Textual dashboard"),
        ("/exit", "Exit ScopeForge session"),
        ("/quit", "Exit ScopeForge session"),
    ]

    def get_completions(self, document, complete_event):
        text = document.text_before_cursor.strip()
        if text.startswith("/"):
            for cmd, desc in self.COMMANDS:
                if cmd.lower().startswith(text.lower()):
                    yield Completion(
                        cmd,
                        start_position=-len(text),
                        display=cmd,
                        display_meta=desc,
                    )


class ScopeForgeCLI:
    """Claude Code & AGY style interactive CLI and one-shot execution engine."""

    def __init__(
        self,
        mode: str = "plan",
        scope: Optional[List[str]] = None,
        model: Optional[str] = None,
        auto_approve: bool = False,
        max_iterations: int = 25,
    ):
        self.console = Console(highlight=True)
        self.sec_mode = mode
        self.auto_approve = auto_approve
        self.max_iterations = max_iterations
        self.total_tokens = 0
        self.estimated_cost = 0.0
        self.chat_history: List[BaseMessage] = []
        self.pending_agent: Optional[str] = None
        self._current_task: Optional[asyncio.Task] = None

        # 1. Initialize Subsystems
        self.provider_mgr = ProviderManager()
        if model:
            self._select_model_by_name(model)

        self.wiki = SecWiki()
        self.rag = LlamaSecRAG()
        self.mcp = MCPBridge()
        self.a2a_bus = A2ABus()
        self.skill_mgr = SkillManager()

        # 2. ScopeGate & Pipeline Configuration
        gate_cfg = ScopeGateConfig.load()
        if scope:
            self.current_scope = scope
        elif gate_cfg.scopes:
            self.current_scope = gate_cfg.scopes
        else:
            self.current_scope = ["authorized.example", "*.example.com", "localhost"]

        self.pipeline = create_default_pipeline(
            mode=self.sec_mode,
            authorized_scopes=self.current_scope,
        )

        # Wire up interactive ApprovalGate callback
        approval_gate = self.pipeline.get_middleware("ApprovalGate")
        if approval_gate and hasattr(approval_gate, "approval_callback"):
            approval_gate.approval_callback = self._terminal_approval_callback
            approval_gate.auto_approve = self.auto_approve

        # 3. LangGraph Orchestrator
        self.orchestrator = MultiAgentSecOpsOrchestrator(
            provider_manager=self.provider_mgr,
            pipeline=self.pipeline,
            rag=self.rag,
            wiki=self.wiki,
            a2a_bus=self.a2a_bus,
            skill_manager=self.skill_mgr,
            mcp_bridge=self.mcp,
        )

        # 4. History persistence
        hist_dir = Path.home() / ".scopeforge"
        hist_dir.mkdir(parents=True, exist_ok=True)
        self.history_file = str(hist_dir / "history")
        self.session: PromptSession = PromptSession(
            history=FileHistory(self.history_file),
            auto_suggest=AutoSuggestFromHistory(),
            completer=ScopeForgeSlashCompleter(),
        )

    def _select_model_by_name(self, model_query: str) -> bool:
        """Find and activate a model matching query or 1-based index."""
        model_query = model_query.strip()
        providers = self.provider_mgr.list_providers()
        # 1. Check numeric index (e.g. /model 1)
        if model_query.isdigit():
            idx = int(model_query) - 1
            if 0 <= idx < len(providers):
                self.provider_mgr.set_active_provider(providers[idx].name)
                return True
            return False

        # 2. Exact match
        query_lower = model_query.lower()
        for cfg in providers:
            if query_lower == cfg.name.lower():
                self.provider_mgr.set_active_provider(cfg.name)
                return True

        # 3. Substring match
        for cfg in providers:
            if query_lower in cfg.name.lower() or query_lower in cfg.model.lower():
                self.provider_mgr.set_active_provider(cfg.name)
                return True
        return False

    def _terminal_approval_callback(self, req: ToolApprovalRequest) -> bool:
        """Inline Human-in-the-Loop (HITL) prompt for sensitive security tools."""
        if self.auto_approve:
            return True

        self.console.print()
        req_body = (
            f"[bold]Tool:[/]     [bold cyan]{req.tool_name}[/]\n"
            f"[bold]Agent:[/]    [bold magenta]{req.agent_name}[/]\n"
            f"[bold]Target:[/]   [yellow]{json.dumps(req.tool_args, indent=2)}[/]\n"
            f"[bold]Severity:[/] [bold red]{req.severity}[/]\n"
            f"[bold]Reason:[/]   [dim]{req.reason}[/]"
        )
        self.console.print(
            Panel(
                req_body,
                title="[bold yellow]⚠️ ScopeGate HITL: Security Action Authorization[/]",
                border_style="yellow",
                box=ROUNDED,
            )
        )

        try:
            choice = input("Authorize this security action? [y=Yes, n=No, a=Always for session] > ").strip().lower()
            if choice in ("a", "always"):
                self.auto_approve = True
                approval_gate = self.pipeline.get_middleware("ApprovalGate")
                if approval_gate:
                    approval_gate.auto_approve = True
                self.console.print("[dim green]✓ Auto-approval enabled for the remainder of this session.[/dim green]")
                return True
            return choice in ("y", "yes")
        except (KeyboardInterrupt, EOFError):
            self.console.print("\n[dim red]Action rejected by operator.[/dim red]")
            return False

    def print_banner(self):
        """Render a clean Claude Code & AGY style header banner."""
        active_cfg = self.provider_mgr.get_active_config()
        mode_color = {
            "plan": "green",
            "audit": "cyan",
            "redteam": "yellow",
            "blueteam": "blue",
            "live": "red",
        }.get(self.sec_mode.lower(), "green")

        scopes_str = ", ".join(self.current_scope[:3])
        if len(self.current_scope) > 3:
            scopes_str += f" (+{len(self.current_scope) - 3} more)"

        cwd_str = os.getcwd()
        if len(cwd_str) > 40:
            cwd_str = "..." + cwd_str[-37:]

        banner_content = (
            f"[bold cyan]⚡ ScopeForge[/bold cyan] [bold white]v{__version__}[/] "
            f"[dim]— Autonomous Cybersecurity Harness[/dim]\n\n"
            f"[bold]Model:[/]  [bold white]{active_cfg.name}[/] [dim]({active_cfg.provider.value}/{active_cfg.model})[/dim]\n"
            f"[bold]Mode:[/]   [bold {mode_color}][{self.sec_mode.upper()}][/] [dim](ScopeGate RoE sandbox)[/dim]\n"
            f"[bold]Scope:[/]  [bold yellow]{scopes_str}[/]\n"
            f"[bold]Dir:[/]    [dim]{cwd_str}[/]\n\n"
            f"[dim]Type [bold white]/help[/] for commands, [bold white]/mode[/] to switch, or enter your mission prompt.[/dim]"
        )

        self.console.print()
        self.console.print(
            Panel(
                banner_content,
                border_style="cyan",
                box=ROUNDED,
                padding=(1, 2),
            )
        )
        self.console.print()

    def get_prompt_text(self) -> HTML:
        """Generate sleek interactive prompt indicator."""
        mode_color = {
            "plan": "ansigreen",
            "audit": "ansicyan",
            "redteam": "ansiyellow",
            "blueteam": "ansiblue",
            "live": "ansired",
        }.get(self.sec_mode.lower(), "ansigreen")

        agent_tag = f" <ansimagenta>[{self.pending_agent}]</ansimagenta>" if self.pending_agent else ""
        return HTML(
            f"<{mode_color}><b>[{self.sec_mode.upper()}]</b></{mode_color}>{agent_tag} <ansibrightcyan><b>❯</b></ansibrightcyan> "
        )

    async def execute_mission(self, prompt: str):
        """Execute a mission prompt through the multi-agent orchestrator with live streaming."""
        forced = self.pending_agent
        self.pending_agent = None

        if forced:
            self.console.print(
                f"[dim magenta]⇋ Supervisor routing directly to {forced.capitalize()}Agent[/dim magenta]"
            )

        start_time = time.time()
        streamed_chars = 0
        current_tool: Optional[str] = None

        def _on_token(agent: str, chunk: str):
            nonlocal streamed_chars, current_tool
            if isinstance(chunk, str) and chunk.startswith('{"__type__":'):
                try:
                    data = json.loads(chunk)
                    evt_type = data.get("__type__")

                    if evt_type == "tool_call":
                        current_tool = data.get("name", "")
                        args = data.get("args", {})
                        self.console.print()
                        self.console.print(f"[bold cyan]● Tool Call:[/] [bold white]{current_tool}[/] [dim]({agent})[/]")
                        if "command" in args:
                            self.console.print(f"  [dim]$[/] [yellow]{args['command']}[/]")
                        elif "file_path" in args:
                            self.console.print(f"  [dim]path:[/] [cyan]{args['file_path']}[/]")
                        elif "target" in args:
                            self.console.print(f"  [dim]target:[/] [yellow]{args['target']}[/]")
                        elif args:
                            preview = json.dumps(args)
                            if len(preview) > 100:
                                preview = preview[:97] + "..."
                            self.console.print(f"  [dim]args:[/] {preview}")
                        return

                    elif evt_type == "tool_result":
                        t_name = data.get("name", current_tool or "Tool")
                        res = data.get("result", "")
                        res_len = len(str(res))
                        self.console.print(f"  [dim green]✓[/dim green] [dim]{t_name} completed ({res_len:,} chars)[/dim]")
                        current_tool = None
                        return

                    elif evt_type == "a2a_banner":
                        sender = data.get("sender", "Supervisor")
                        recipient = data.get("recipient", "Agent")
                        intent = data.get("intent", "TASK_DELEGATION")
                        prev = data.get("preview", "")
                        self.console.print(
                            f"[dim magenta]─── ⇋ {sender} → {recipient} [{intent}] ───[/dim magenta]"
                        )
                        if prev:
                            self.console.print(f"[dim]    {prev}[/dim]")
                        return

                except Exception:
                    pass

            # Stream LLM response text chunk directly to terminal stdout
            streamed_chars += len(chunk)
            sys.stdout.write(chunk)
            sys.stdout.flush()

        try:
            self.console.print()
            state = await self.orchestrator.run(
                user_message=prompt,
                mode=self.sec_mode,
                scope=self.current_scope,
                history=self.chat_history,
                forced_agent=forced,
                on_token=_on_token,
                max_iterations=self.max_iterations,
            )

            # Flush newline after streaming
            if streamed_chars > 0:
                sys.stdout.write("\n")
                sys.stdout.flush()

            active_agent = state.get("active_agent", "Supervisor")

            # If nothing was streamed (e.g. cached or direct final message), render markdown
            if streamed_chars == 0 and state.get("messages"):
                last_msg = state["messages"][-1]
                content = str(last_msg.content)
                self.console.print()
                self.console.print(Markdown(content))

            # Render Findings cards if discovered
            findings = state.get("findings", [])
            if findings:
                self.console.print()
                for f in findings:
                    sev = str(f.get("severity", "MEDIUM")).upper()
                    f_id = f.get("id", "SF-FINDING")
                    title = f.get("title", "Security Finding")
                    desc = f.get("description", "")
                    cvss = f.get("cvss_score", "N/A")

                    badge_color = "red" if sev in ("CRITICAL", "HIGH") else "yellow" if sev == "MEDIUM" else "cyan"
                    body = (
                        f"[bold]Target:[/]   [yellow]{f.get('target', 'N/A')}[/]\n"
                        f"[bold]Severity:[/] [{badge_color}]{sev} (CVSS {cvss})[/{badge_color}]\n"
                        f"[bold]Details:[/]  {desc}"
                    )
                    self.console.print(
                        Panel(
                            body,
                            title=f"[{badge_color}]🚨 {f_id}: {title}[/{badge_color}]",
                            border_style=badge_color,
                            box=ROUNDED,
                        )
                    )

            # Update conversation history
            if state.get("messages"):
                new_msgs = state["messages"][-2:]
                if len(self.chat_history) < 2 or self.chat_history[-2:] != new_msgs:
                    self.chat_history.extend(new_msgs)
                    if len(self.chat_history) > 10:
                        self.chat_history = self.chat_history[-10:]

            # Stats summary
            elapsed = time.time() - start_time
            turn_tokens = max(1, len(prompt) // 4) + 350
            self.total_tokens += turn_tokens
            turn_cost = 0.001 if "free" in self.provider_mgr.active_provider_name else 0.003
            self.estimated_cost += turn_cost

            self.console.print()
            self.console.print(
                f"[dim]● Tokens: {self.total_tokens:,} | Cost: ${self.estimated_cost:.4f} | Time: {elapsed:.2f}s | Agent: {active_agent}[/dim]"
            )
            self.console.print()

        except (KeyboardInterrupt, asyncio.CancelledError):
            self.console.print("\n[bold yellow]⚠️ Task interrupted by operator (<kbd>Ctrl+C</kbd>).[/bold yellow]\n")
            return
        except Exception as e:
            self.console.print(f"\n[bold red]❌ Multi-agent execution error: {e}[/bold red]\n")

    def handle_slash_command(self, raw: str) -> bool:
        """Process slash commands. Returns True if command was handled, False to exit."""
        parts = raw.strip().split()
        cmd = parts[0].lower()
        args = parts[1:]

        if cmd in ("/exit", "/quit"):
            self.console.print("[dim cyan]Exiting ScopeForge. Have a secure day![/dim cyan]")
            return False

        elif cmd == "/clear":
            self.console.clear()
            self.print_banner()

        elif cmd == "/help":
            self._show_help()

        elif cmd == "/mode":
            if not args:
                self.console.print(f"[bold]Current ScopeGate Mode:[/] [bold cyan]{self.sec_mode.upper()}[/]")
                self.console.print("[dim]Options: plan, audit, redteam, blueteam, live[/dim]")
            else:
                target_mode = args[0].lower()
                valid_modes = ["plan", "audit", "redteam", "blueteam", "live"]
                if target_mode in valid_modes:
                    self.sec_mode = target_mode
                    self.pipeline.set_mode(target_mode)
                    self.console.print(f"[dim green]✓ ScopeGate mode switched to:[/] [bold]{target_mode.upper()}[/]")
                else:
                    self.console.print(f"[bold red]Unknown mode:[/] {target_mode}. Choose from: {', '.join(valid_modes)}")

        elif cmd in ("/model", "/models"):
            if not args:
                self._show_models()
            elif args[0].lower() == "add":
                self._handle_model_add(args[1:])
            else:
                query = " ".join(args)
                if self._select_model_by_name(query):
                    active = self.provider_mgr.get_active_config()
                    self.console.print(f"[dim green]✓ Active model switched to:[/] [bold white]{active.name}[/] [dim]({active.model})[/dim]")
                else:
                    self.console.print(f"[bold red]No matching model found for:[/] '{query}'. Type [bold white]/model[/] to see options or [bold white]/model add[/] to add one.")

        elif cmd == "/config":
            self._handle_config(args)

        elif cmd == "/scope":
            gate = self.pipeline.get_scope_gate()
            if not args:
                scopes = sorted(list(gate.authorized_scopes)) if gate else self.current_scope
                self.console.print("[bold]Authorized Targets & Scopes:[/]")
                for s in scopes:
                    self.console.print(f"  • [yellow]{s}[/]")
                self.console.print("[dim]Add scope with: /scope <domain or IP>[/dim]")
            else:
                target = args[0].strip()
                if gate:
                    gate.add_scope(target)
                if target not in self.current_scope:
                    self.current_scope.append(target)
                self.console.print(f"[dim green]✓ Added target to authorized scopes:[/] [bold yellow]{target}[/]")

        elif cmd == "/agent":
            if not args:
                self.console.print("[bold]Direct Agent Routing:[/]")
                self.console.print("  • /agent recon    — Target surface mapping & OSINT")
                self.console.print("  • /agent audit    — Code review & vulnerability discovery")
                self.console.print("  • /agent exploit  — Safe falsifiable PoC verification")
                self.console.print("  • /agent dev      — Workspace code editing & git tools")
                self.console.print("  • /agent report   — Executive & technical reporting")
            else:
                agent_name = args[0].lower()
                valid_agents = ["recon", "audit", "exploit", "dev", "report", "supervisor"]
                if agent_name in valid_agents:
                    self.pending_agent = agent_name
                    self.console.print(f"[dim cyan]Next prompt will be routed directly to:[/] [bold magenta]{agent_name.capitalize()}Agent[/]")
                else:
                    self.console.print(f"[bold red]Unknown agent:[/] {agent_name}. Valid agents: {', '.join(valid_agents)}")

        elif cmd == "/diff":
            from .sec_tools.code_tools import git_diff_tool
            res = json.loads(git_diff_tool.invoke({"staged": False}))
            diff_text = res.get("diff", "")
            if res.get("has_changes"):
                self.console.print()
                self.console.print(Syntax(diff_text, "diff", theme="monokai", line_numbers=True))
                self.console.print()
            else:
                self.console.print("[dim green]✓ Working tree is clean. No uncommitted modifications.[/dim green]")

        elif cmd == "/commit":
            from .sec_tools.code_tools import git_commit_tool
            msg = " ".join(args).strip()
            if not msg:
                self.console.print("[yellow]Usage: /commit <message>[/yellow]")
            else:
                res = json.loads(git_commit_tool.invoke({"message": msg}))
                if res.get("success"):
                    self.console.print(f"[dim green]✓ Committed changes:[/] [bold]{msg}[/]")
                    if res.get("stdout"):
                        self.console.print(f"[dim]{res.get('stdout').strip()}[/dim]")
                else:
                    self.console.print(f"[bold red]❌ Commit failed:[/] {res.get('stderr') or res.get('error')}")

        elif cmd == "/review":
            from .sec_tools.code_tools import git_diff_tool
            res = json.loads(git_diff_tool.invoke({"staged": False}))
            diff_text = res.get("diff", "")
            if not res.get("has_changes"):
                self.console.print("[dim green]✓ No workspace modifications to review. Working directory is clean.[/dim green]")
            else:
                self.console.print("[dim cyan]Starting autonomous security review on current git diff...[/dim cyan]")
                review_prompt = f"Perform a comprehensive Claude Code style code review on this git diff:\n```diff\n{diff_text[:4000]}\n```"
                try:
                    loop = asyncio.get_running_loop()
                except RuntimeError:
                    loop = None

                if loop and loop.is_running():
                    loop.create_task(self.execute_mission(review_prompt))
                else:
                    asyncio.run(self.execute_mission(review_prompt))

        elif cmd == "/init":
            p = Path("SCOPEFORGE.md")
            if p.exists():
                self.console.print("[yellow]ℹ️ `SCOPEFORGE.md` already exists in this directory.[/yellow]")
            else:
                content = (
                    "# ScopeForge Project Guidelines\n\n"
                    "## Operational Rules\n"
                    "- Maintain strict ScopeGate boundaries.\n"
                    "- Only test authorized targets.\n"
                    "- Verify findings with falsifiable proof-of-concept before reporting.\n"
                )
                p.write_text(content, encoding="utf-8")
                self.console.print("[dim green]✓ Initialized `SCOPEFORGE.md` project memory in repository root.[/dim green]")

        elif cmd == "/doctor":
            self._show_doctor()

        elif cmd == "/compact":
            if len(self.chat_history) <= 2:
                self.console.print("[dim]Conversation context is already minimal.[/dim]")
            else:
                old_len = len(self.chat_history)
                last_turn = self.chat_history[-2:]
                self.chat_history = [
                    HumanMessage(content="[Context Summary: Previous turns compacted to conserve token budget.]"),
                    AIMessage(content="Understood. Previous context compacted."),
                ] + last_turn
                self.console.print(f"[dim green]✓ Context compacted from {old_len} to {len(self.chat_history)} messages.[/dim green]")

        elif cmd == "/cost":
            self.console.print("[bold]Session Usage & Cost Metrics:[/]")
            self.console.print(f"  • [bold]Estimated Tokens:[/] {self.total_tokens:,}")
            self.console.print(f"  • [bold]Estimated Cost:[/]   ${self.estimated_cost:.4f}")
            self.console.print(f"  • [bold]Active Model:[/]     {self.provider_mgr.get_active_config().name}")

        elif cmd == "/wiki":
            query = " ".join(args).strip()
            pages = self.wiki.list_pages()
            if not query:
                self.console.print(f"[bold]SecWiki Knowledge Pages ({len(pages)}):[/]")
                for p in pages[:15]:
                    self.console.print(f"  • [cyan]{p}[/]")
                self.console.print("[dim]Read or search a page with: /wiki <page_name or keyword>[/dim]")
            else:
                matches = [p for p in pages if query.lower() in p.lower()]
                if matches:
                    self.console.print(f"[bold]SecWiki Matches for '{query}':[/]")
                    for m in matches[:5]:
                        content = self.wiki.read_page(m) or ""
                        preview = content[:200].replace("\n", " ")
                        self.console.print(f"  • [bold cyan]{m}:[/] [dim]{preview}...[/dim]")
                else:
                    self.console.print(f"[dim]No wiki pages matching '{query}'. Available: {', '.join(pages[:5])}[/dim]")

        elif cmd == "/rag":
            query = " ".join(args).strip()
            if not query:
                self.console.print("[yellow]Usage: /rag <search query>[/yellow]")
            else:
                results = self.rag.query(query)
                self.console.print(f"[bold]LlamaIndex RAG Matches ({len(results)} found):[/]")
                for idx, r in enumerate(results[:3], 1):
                    txt = r.get("text", "") if isinstance(r, dict) else str(r)
                    self.console.print(f"\n[bold cyan]Match {idx}:[/]\n[dim]{txt[:300]}...[/dim]")

        elif cmd in ("/tui", "/dashboard", "/gui"):
            self.console.print("[dim cyan]Switching to full-screen Textual dashboard...[/dim cyan]")
            from .tui.app import ScopeForgeTUIApp
            app = ScopeForgeTUIApp()
            try:
                loop = asyncio.get_running_loop()
            except RuntimeError:
                loop = None

            if loop and loop.is_running():
                loop.create_task(app.run_async())
            else:
                try:
                    app.run()
                except (KeyboardInterrupt, EOFError):
                    pass
            self.console.clear()
            self.print_banner()

        else:
            self.console.print(f"[bold red]Unknown command:[/] `{cmd}`. Type [bold white]/help[/] for available commands.")

        return True

    def _show_help(self):
        """Display help panel with organized slash commands."""
        table = Table(box=ROUNDED, border_style="cyan", show_header=True)
        table.add_column("Command", style="bold white", width=18)
        table.add_column("Description", style="dim white")

        table.add_row("/help", "Show this command reference")
        table.add_row("/mode [name]", "Switch ScopeGate mode: plan, audit, redteam, live")
        table.add_row("/model [name|#]", "Switch active LLM provider or model")
        table.add_row("/model add", "Register custom LLM profile (wizard or inline)")
        table.add_row("/config [set ...]", "View or update provider key, model, or base URL")
        table.add_row("/scope [target]", "Inspect or add authorized domain/IP targets")
        table.add_row("/agent [name]", "Route directly to recon, audit, exploit, dev")
        table.add_row("/diff", "View color syntax git diff of workspace changes")
        table.add_row("/commit <msg>", "Stage and commit changes using git")
        table.add_row("/review", "Autonomous security code review of git diff")
        table.add_row("/init", "Create SCOPEFORGE.md project instructions")
        table.add_row("/doctor", "Check environment, platform, models, and tools")
        table.add_row("/compact", "Compact conversation context to save tokens")
        table.add_row("/cost", "Display session token usage and estimated cost")
        table.add_row("/wiki [query]", "Search security wiki knowledge base")
        table.add_row("/rag [query]", "Search LlamaIndex cybersecurity RAG store")
        table.add_row("/clear", "Clear terminal screen")
        table.add_row("/tui", "Switch to full-screen multi-pane Textual dashboard")
        table.add_row("/exit, /quit", "Exit ScopeForge")

        self.console.print()
        self.console.print(table)
        self.console.print()

    def _show_models(self):
        """Display all configured models in a clean, robust table."""
        providers = self.provider_mgr.list_providers()
        active_name = self.provider_mgr.active_provider_name

        try:
            table = Table(title="Configured LLM Models", box=ROUNDED, border_style="cyan")
            table.add_column("#", width=4, justify="right")
            table.add_column("Name", style="bold white", min_width=18)
            table.add_column("Provider", style="cyan", min_width=10)
            table.add_column("Model ID", style="dim", min_width=20)
            table.add_column("Status", min_width=10)

            for idx, cfg in enumerate(providers):
                is_active = cfg.name == active_name
                status = "[bold green]ACTIVE[/bold green]" if is_active else "[dim]available[/dim]"
                table.add_row(str(idx + 1), cfg.name, cfg.provider.value, cfg.model, status)

            self.console.print()
            self.console.print(table)
            self.console.print("[dim]Switch: [bold white]/model <name or #>[/bold white] | Add custom: [bold white]/model add[/bold white] | Config: [bold white]/config[/bold white][/dim]\n")
        except Exception:
            # Bulleted fallback in case terminal width or buffer encounters stream limits
            self.console.print(f"\n[bold]Configured LLM Models ({len(providers)}):[/]")
            for idx, cfg in enumerate(providers, 1):
                star = "★ " if cfg.name == active_name else "  "
                self.console.print(f" {star}{idx:2d}. [bold]{cfg.name}[/] ({cfg.provider.value} / {cfg.model})")
            self.console.print("[dim]Switch model with: /model <name or number> | /model add[/dim]\n")

    def _handle_model_add(self, args: List[str]):
        """Register a new custom model either via arguments or interactive wizard."""
        name = ""
        model_id = ""
        base_url = ""
        api_key = ""
        provider_type = "custom"

        if args and any(arg.startswith("--") for arg in args):
            import argparse
            add_parser = argparse.ArgumentParser(prog="/model add", add_help=False)
            add_parser.add_argument("--name", default="")
            add_parser.add_argument("--model", default="")
            add_parser.add_argument("--base", default="")
            add_parser.add_argument("--key", default="")
            add_parser.add_argument("--provider", default="custom")
            try:
                parsed, _ = add_parser.parse_known_args(args)
                name = parsed.name
                model_id = parsed.model
                base_url = parsed.base
                api_key = parsed.key
                provider_type = parsed.provider
            except Exception:
                pass
        elif len(args) >= 2:
            name = args[0].strip()
            model_id = args[1].strip()
            if len(args) >= 3:
                base_url = args[2].strip()
            if len(args) >= 4:
                api_key = args[3].strip()
            if len(args) >= 5:
                provider_type = args[4].strip()

        # If missing name or model_id, run interactive wizard
        if not name or not model_id:
            self.console.print()
            self.console.print(
                Panel(
                    "[bold cyan]⚡ ScopeForge Custom Model Setup Wizard[/bold cyan]\n"
                    "[dim]Configure any OpenAI-compatible API, Anthropic proxy, Ollama, vLLM, or OpenRouter endpoint.[/dim]",
                    box=ROUNDED,
                    border_style="cyan",
                )
            )
            try:
                if not name:
                    name = input("  1. Model Name (e.g. custom-claude, deepseek-chat): ").strip()
                    if not name:
                        self.console.print("[yellow]⚠️ Setup cancelled: Model name is required.[/yellow]\n")
                        return

                if not provider_type or provider_type == "custom":
                    pt_input = input("  2. Provider Type [custom, anthropic, openai, ollama, groq, openrouter] (default: custom): ").strip().lower()
                    if pt_input:
                        provider_type = pt_input

                if not model_id:
                    model_id = input("  3. Model ID (e.g. claude-3-7-sonnet-20250219, gpt-4o, deepseek-chat): ").strip()
                    if not model_id:
                        self.console.print("[yellow]⚠️ Setup cancelled: Model ID is required.[/yellow]\n")
                        return

                if not base_url:
                    base_url = input("  4. Base URL (e.g. https://api.justwoker.icu, http://localhost:11434/v1, or press Enter): ").strip()

                if not api_key:
                    api_key = input("  5. API Key (e.g. sk-..., env:KEY_NAME, or press Enter to skip): ").strip()

            except (KeyboardInterrupt, EOFError):
                self.console.print("\n[dim yellow]Model setup cancelled.[/dim yellow]\n")
                return

        try:
            cfg = self.provider_mgr.add_custom_provider(
                name=name,
                model=model_id,
                api_key=api_key or None,
                api_base=base_url or None,
                provider=provider_type,
                set_active=True,
            )
            self.console.print()
            self.console.print(
                Panel(
                    f"[bold green]✓ Custom model registered and set to ACTIVE![/bold green]\n\n"
                    f"  • [bold]Name:[/]        [bold white]{cfg.name}[/]\n"
                    f"  • [bold]Provider:[/]    [cyan]{cfg.provider.value}[/]\n"
                    f"  • [bold]Model ID:[/]    [yellow]{cfg.model}[/]\n"
                    f"  • [bold]Base URL:[/]    [dim]{cfg.api_base or '(default provider endpoint)'}[/dim]\n"
                    f"  • [bold]API Key:[/]     [dim]{'••••••••' if cfg.api_key else '(none / inherited from environment)'}[/dim]\n"
                    f"  • [bold]Saved to:[/]    [dim]{self.provider_mgr.config_path}[/dim]",
                    title="[bold green]Model Activated[/bold green]",
                    border_style="green",
                    box=ROUNDED,
                )
            )
            self.console.print()
        except Exception as e:
            self.console.print(f"\n[bold red]❌ Failed to register model:[/] {e}\n")

    def _handle_config(self, args: List[str]):
        """Inspect or configure active provider keys, models, or base endpoints."""
        if not args:
            active = self.provider_mgr.get_active_config()
            self.console.print()
            self.console.print(
                Panel(
                    f"[bold]Active Model:[/]  [bold white]{active.name}[/]\n"
                    f"[bold]Provider:[/]      [cyan]{active.provider.value}[/]\n"
                    f"[bold]Model ID:[/]      [yellow]{active.model}[/]\n"
                    f"[bold]Base URL:[/]      [dim]{active.api_base or '(default endpoint)'}[/dim]\n"
                    f"[bold]API Key:[/]       [dim]{'••••••••' if active.api_key else '(none / from env)'}[/dim]\n\n"
                    f"[dim]Quick updates for active model:[/dim]\n"
                    f"  • [white]/config set key <API_KEY>[/white]\n"
                    f"  • [white]/config set model <MODEL_ID>[/white]\n"
                    f"  • [white]/config set base <BASE_URL>[/white]\n"
                    f"  • [white]/model add[/white] (to register a new model profile)",
                    title="[bold cyan]Provider Configuration[/bold cyan]",
                    border_style="cyan",
                    box=ROUNDED,
                )
            )
            self.console.print()
            return

        if args[0].lower() == "set" and len(args) >= 3:
            sub = args[1].lower()
            val = " ".join(args[2:]).strip()
            active = self.provider_mgr.get_active_config()
            if sub in ("key", "api_key", "apikey"):
                active.api_key = self.provider_mgr._sanitize_api_key(val)
                self.provider_mgr.save_config()
                self.console.print(f"[dim green]✓ Updated API key for active model '{active.name}'.[/dim green]")
            elif sub in ("model", "model_id"):
                active.model = val
                self.provider_mgr.save_config()
                self.console.print(f"[dim green]✓ Updated model ID for active model '{active.name}' to '{val}'.[/dim green]")
            elif sub in ("base", "base_url", "url"):
                active.api_base = self.provider_mgr._normalize_base_for_provider(active.provider, val)
                self.provider_mgr.save_config()
                self.console.print(f"[dim green]✓ Updated base URL for active model '{active.name}' to '{active.api_base}'.[/dim green]")
            else:
                self.console.print(f"[yellow]Unknown config property:[/] '{sub}'. Choose: key, model, or base.")
        else:
            self.console.print("[yellow]Usage: /config set <key|model|base> <value>[/yellow]")

    def _show_doctor(self):
        """Run system diagnostics."""
        active_cfg = self.provider_mgr.get_active_config()
        self.console.print()
        self.console.print(
            Panel(
                f"[bold cyan]ScopeForge Diagnostics (Doctor)[/bold cyan]\n\n"
                f"[bold]Python:[/]      `{platform.python_version()}` ({sys.executable})\n"
                f"[bold]Platform:[/]    `{platform.system()} {platform.release()}`\n"
                f"[bold]Active Model:[/] `{active_cfg.name}` ({active_cfg.provider.value} / {active_cfg.model})\n"
                f"[bold]ScopeGate:[/]    Mode: `[{self.sec_mode.upper()}]` | Scopes: `{', '.join(self.current_scope)}`\n"
                f"[bold]Skills:[/]       `{len(self.skill_mgr.list_skills())}` skills discovered\n"
                f"[bold]MCP:[/]          `{len(self.mcp.list_servers())}` servers configured\n"
                f"[bold]RAG Store:[/]    LlamaIndex online ({len(self.rag.documents)} chunks)\n"
                f"[bold]Status:[/]       [bold green]● All systems nominal and ready for operations[/bold green]",
                box=ROUNDED,
                border_style="cyan",
            )
        )
        self.console.print()

    async def run_repl(self):
        """Run the main interactive REPL loop."""
        self.print_banner()
        last_ctrl_c_time = 0.0

        while True:
            try:
                user_input = await self.session.prompt_async(self.get_prompt_text())
                last_ctrl_c_time = 0.0
                text = user_input.strip()
                if not text:
                    continue

                if text.lower() in ("tui", "dashboard", "gui", "/tui", "/dashboard", "/gui"):
                    self.console.print("[dim cyan]Switching to full-screen Textual dashboard...[/dim cyan]")
                    try:
                        from .tui.app import ScopeForgeTUIApp
                        app = ScopeForgeTUIApp()
                        await app.run_async()
                    except (KeyboardInterrupt, asyncio.CancelledError):
                        pass
                    except Exception as e:
                        self.console.print(f"[bold red]❌ Dashboard error: {e}[/bold red]")
                    finally:
                        self.console.clear()
                        self.print_banner()
                    continue

                if text.lower() == "help":
                    self._show_help()
                    continue

                if text.lower() == "/review":
                    from .sec_tools.code_tools import git_diff_tool
                    res = json.loads(git_diff_tool.invoke({"staged": False}))
                    diff_text = res.get("diff", "")
                    if not res.get("has_changes"):
                        self.console.print("[dim green]✓ No workspace modifications to review. Working directory is clean.[/dim green]")
                    else:
                        self.console.print("[dim cyan]Starting autonomous security review on current git diff...[/dim cyan]")
                        self._current_task = asyncio.create_task(
                            self.execute_mission(f"Perform a comprehensive Claude Code style code review on this git diff:\n```diff\n{diff_text[:4000]}\n```")
                        )
                        try:
                            await self._current_task
                        except (KeyboardInterrupt, asyncio.CancelledError):
                            if self._current_task and not self._current_task.done():
                                self._current_task.cancel()
                                try:
                                    await asyncio.wait_for(asyncio.shield(self._current_task), timeout=0.8)
                                except (asyncio.CancelledError, asyncio.TimeoutError, KeyboardInterrupt, Exception):
                                    pass
                            self.console.print("\n[bold yellow]⚠️ Review interrupted by operator (<kbd>Ctrl+C</kbd>).[/bold yellow]\n")
                        finally:
                            self._current_task = None
                    continue

                if text.startswith("/"):
                    should_continue = self.handle_slash_command(text)
                    if not should_continue:
                        break
                    continue

                # Run mission through orchestrator
                self._current_task = asyncio.create_task(self.execute_mission(text))
                try:
                    await self._current_task
                except (KeyboardInterrupt, asyncio.CancelledError):
                    if self._current_task and not self._current_task.done():
                        self._current_task.cancel()
                        try:
                            await asyncio.wait_for(asyncio.shield(self._current_task), timeout=0.8)
                        except (asyncio.CancelledError, asyncio.TimeoutError, KeyboardInterrupt, Exception):
                            pass
                    self.console.print("\n[bold yellow]⚠️ Task interrupted by operator (<kbd>Ctrl+C</kbd>).[/bold yellow]\n")
                finally:
                    self._current_task = None

            except KeyboardInterrupt:
                # Ctrl+C at prompt: exit cleanly if pressed twice within 2s, else notify
                now = time.time()
                if now - last_ctrl_c_time < 2.0:
                    self.console.print("\n[dim cyan]Exiting ScopeForge. Goodbye![/dim cyan]")
                    break
                last_ctrl_c_time = now
                self.console.print("\n[dim](Press Ctrl+C again or type /exit to quit)[/dim]")
                continue
            except EOFError:
                # Ctrl+D at prompt: exit cleanly
                self.console.print("\n[dim cyan]Exiting ScopeForge. Goodbye![/dim cyan]")
                break


def main():
    """Main CLI entrypoint for scopeforge / sf / agi."""
    parser = argparse.ArgumentParser(
        prog="scopeforge",
        description="ScopeForge — Commercial-Grade Cybersecurity Multi-Agent Harness & Interactive CLI",
    )
    parser.add_argument(
        "query",
        nargs="?",
        default=None,
        help="Optional mission prompt for direct non-interactive execution",
    )
    parser.add_argument(
        "-m",
        "--mode",
        default="plan",
        choices=["plan", "audit", "redteam", "blueteam", "live"],
        help="ScopeGate operational mode (default: plan)",
    )
    parser.add_argument(
        "-s",
        "--scope",
        nargs="+",
        default=None,
        help="Authorized target domains or IPs (e.g. -s thangarasusamayal.vercel.app)",
    )
    parser.add_argument(
        "--model",
        default=None,
        help="Target LLM model name (e.g. claude-3-7-sonnet, gpt-4o, deepseek-r1)",
    )
    parser.add_argument(
        "-a",
        "--agent",
        default=None,
        choices=["recon", "audit", "exploit", "dev", "report", "supervisor"],
        help="Direct initial prompt to a specific agent",
    )
    parser.add_argument(
        "-i",
        "--iterations",
        type=int,
        default=25,
        help="Maximum autonomous reasoning loops (default: 25)",
    )
    parser.add_argument(
        "--auto-approve",
        action="store_true",
        help="Auto-approve sensitive security actions without interactive confirmation",
    )
    parser.add_argument(
        "--tui",
        action="store_true",
        help="Launch legacy full-screen Textual dashboard instead of interactive CLI",
    )
    parser.add_argument(
        "-v",
        "--version",
        action="version",
        version=f"ScopeForge v{__version__}",
    )

    args = parser.parse_args()

    # Route to legacy Textual full-screen dashboard if requested via flag, argument, or env var
    query_raw = (args.query or "").strip().lower()
    if args.tui or query_raw in ("tui", "--tui", "dashboard", "gui") or os.environ.get("SCOPEFORGE_UI") == "tui":
        from .tui.app import ScopeForgeTUIApp
        app = ScopeForgeTUIApp()
        try:
            app.run()
        except (KeyboardInterrupt, EOFError):
            pass
        return

    # Use standard Python asyncio event loop (uvloop has terminal/termios signal issues with prompt_toolkit)
    cli = ScopeForgeCLI(
        mode=args.mode,
        scope=args.scope,
        model=args.model,
        auto_approve=args.auto_approve,
        max_iterations=args.iterations,
    )
    if args.agent:
        cli.pending_agent = args.agent

    try:
        # Direct one-shot mission execution (e.g. `scopeforge "audit target.com"`)
        if args.query:
            cli.print_banner()
            asyncio.run(cli.execute_mission(args.query))
        else:
            # Interactive Claude Code / AGY REPL
            asyncio.run(cli.run_repl())
    except (KeyboardInterrupt, EOFError):
        print("\nExiting ScopeForge. Goodbye!")
        sys.exit(0)


if __name__ == "__main__":
    main()
