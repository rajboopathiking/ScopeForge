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
from rich.box import ROUNDED, HEAVY_EDGE, SIMPLE_HEAVY, MINIMAL
from rich.console import Console
from rich.live import Live
from rich.markdown import Markdown
from rich.panel import Panel
from rich.rule import Rule
from rich.syntax import Syntax
from rich.table import Table
from rich.text import Text

from . import __version__
from .a2a.bus import A2ABus
from .a2a.protocol import A2AIntent
from .agents.graph import MultiAgentSecOpsOrchestrator, _clean_tool_call_text
from .config.loader import ScopeGateConfig
from .llm_providers.manager import ProviderManager
from .mcp_bridge import MCPBridge
from .middleware.base import ToolApprovalRequest
from .middleware.pipeline import MiddlewarePipeline, create_default_pipeline
from .rag.engine import LlamaSecRAG
from .skills.manager import SkillManager
from .wiki.store import SecWiki


def _display_model_id(cfg: Any) -> str:
    """Render `provider/model` without doubling when the slug already includes it
    (e.g. provider=openrouter + model=openrouter/free → openrouter/free)."""
    provider = getattr(cfg.provider, "value", str(cfg.provider))
    model = str(cfg.model or "")
    if model.startswith(provider + "/"):
        return model
    return f"{provider}/{model}"


def _format_provider_badge(provider_val: str) -> str:
    """Render a styled color pill for LLM provider types."""
    p = str(provider_val).lower()
    if "anthropic" in p:
        return "[bold bright_cyan]Anthropic[/]"
    elif "openai" in p:
        return "[bold bright_green]OpenAI[/]"
    elif "openrouter" in p:
        return "[bold magenta]OpenRouter[/]"
    elif "groq" in p:
        return "[bold yellow]Groq[/]"
    elif "ollama" in p:
        return "[bold green]Ollama[/]"
    elif "gemini" in p:
        return "[bold blue]Gemini[/]"
    elif "custom" in p:
        return "[bold cyan]Custom[/]"
    return f"[dim]{provider_val}[/]"


def _infer_model_tier(model_name: str, model_id: str) -> str:
    """Infer capability tier badge for commercial display."""
    combined = (str(model_name) + " " + str(model_id)).lower()
    if "free" in combined or ":free" in combined:
        return "[green]Free Tier[/]"
    elif "opus" in combined:
        return "[bold yellow]Frontier (Opus)[/]"
    elif "sonnet" in combined:
        return "[bold cyan]Frontier (Sonnet)[/]"
    elif "gpt-4" in combined or "o1" in combined or "o3" in combined:
        return "[bold green]Frontier (GPT-4o)[/]"
    elif "groq" in combined or "nemotron" in combined:
        return "[bold bright_yellow]Ultra-Fast[/]"
    elif "ollama" in combined or "local" in combined:
        return "[bold blue]Local Offline[/]"
    elif "deepseek" in combined:
        return "[bold bright_cyan]Reasoning / Code[/]"
    return "[dim]Standard[/]"


def _format_tool_call_card(tool_name: str, args: Dict[str, Any], agent: str) -> Panel:
    """Render a sleek, commercial-grade tool invocation card."""
    t_lower = tool_name.lower()
    if "bash" in t_lower or "shell" in t_lower or "terminal" in t_lower:
        icon = "⚡"
        category = "Terminal Execution"
        border_color = "bright_cyan"
    elif "file" in t_lower or "read" in t_lower or "write" in t_lower or "append" in t_lower:
        icon = "📄"
        category = "File Operation"
        border_color = "blue"
    elif "git" in t_lower:
        icon = "🌿"
        category = "Git Operation"
        border_color = "bright_green"
    elif "search" in t_lower or "google" in t_lower or "web" in t_lower:
        icon = "🌐"
        category = "Web Search"
        border_color = "cyan"
    elif "scan" in t_lower or "nmap" in t_lower or "recon" in t_lower or "cve" in t_lower:
        icon = "🛡️"
        category = "Security Recon"
        border_color = "yellow"
    elif "screenshot" in t_lower:
        icon = "📸"
        category = "Screen Capture"
        border_color = "magenta"
    elif "mcp" in t_lower or "server" in t_lower:
        icon = "🔌"
        category = "MCP Bridge"
        border_color = "bright_magenta"
    elif "skill" in t_lower:
        icon = "🧠"
        category = "Skill Manager"
        border_color = "bright_blue"
    else:
        icon = "⚙️"
        category = "Tool Execution"
        border_color = "cyan"

    lines: List[str] = []
    if "command" in args and ("bash" in t_lower or "terminal" in t_lower or "shell" in t_lower or "exec" in t_lower):
        cmd_str = str(args["command"])
        lines.append(f"[bold green]❯[/] [bold bright_yellow]{cmd_str}[/]")
        if "working_dir" in args or "cwd" in args:
            cwd = args.get("working_dir") or args.get("cwd")
            lines.append(f"  [dim]cwd:[/] [dim white]{cwd}[/]")
    elif "name" in args and ("command" in args or "server" in t_lower or "mcp" in t_lower):
        lines.append(f"[dim]server:[/]  [bold bright_magenta]{args.get('name')}[/]")
        if "command" in args:
            lines.append(f"[dim]command:[/] [bold yellow]{args['command']}[/]")
    elif "repo_url" in args:
        lines.append(f"[dim]repo:[/]    [bold cyan]{args['repo_url']}[/]")
    elif "file_path" in args or "path" in args or "target_file" in args:
        fpath = args.get("file_path") or args.get("path") or args.get("target_file")
        lines.append(f"[dim]path:[/] [bold bright_cyan]{fpath}[/]")
        if "code_content" in args or "content" in args or "replacement_content" in args:
            c = str(args.get("code_content") or args.get("content") or args.get("replacement_content") or "")
            c_lines = len(c.splitlines())
            lines.append(f"[dim]payload:[/] [cyan]{len(c):,} bytes[/] · [dim]lines:[/] [cyan]{c_lines}[/]")
        if "start_line" in args:
            lines.append(f"[dim]range:[/] [cyan]lines {args.get('start_line')}-{args.get('end_line', 'end')}[/]")
    elif "query" in args:
        lines.append(f"[dim]query:[/] [bold bright_white]{args['query']}[/]")
    elif "target" in args:
        lines.append(f"[dim]target:[/] [bold yellow]{args['target']}[/]")
        if "ports" in args:
            lines.append(f"[dim]ports:[/]  [cyan]{args['ports']}[/]")
    elif "url" in args:
        lines.append(f"[dim]url:[/]    [bold cyan]{args['url']}[/]")
        if "output_file" in args:
            lines.append(f"[dim]save_to:[/] [white]{args['output_file']}[/]")
    else:
        if args:
            for k, v in list(args.items())[:6]:
                v_str = str(v)
                if len(v_str) > 75:
                    v_str = v_str[:72] + "..."
                lines.append(f"[dim]{k}:[/] [white]{v_str}[/]")
        else:
            lines.append("[dim italic]no parameters[/]")

    body = "\n".join(lines)
    header = f"[bold white]{icon} Tool Call:[/] [bold bright_cyan]{tool_name}[/] [dim]· {category} · {agent.capitalize()}Agent[/]"
    return Panel(
        body,
        title=f" {header} ",
        title_align="left",
        border_style=border_color,
        box=ROUNDED,
        padding=(0, 1),
    )


def _format_tool_result_card(tool_name: str, result: str, duration: float = 0.0) -> Panel:
    """Render an elegant tool result card showing observed output lines."""
    res_str = str(result or "").strip()
    is_error = any(
        err_cue in res_str.lower()
        for err_cue in (
            "error:",
            "traceback (most recent call last):",
            "command failed",
            "tool execution error",
            "not registered",
            "failed with exit code",
            "exit code 1",
            "command not found",
        )
    )
    status_icon = "✗" if is_error else "✓"
    status_color = "bold red" if is_error else "bold green"
    border_color = "red" if is_error else "bright_black"

    time_str = f" in {duration:.2f}s" if duration > 0 else ""

    if not res_str:
        body = f"[dim italic]Completed with empty stdout{time_str}[/]"
        return Panel(
            body,
            title=f" [{status_color}]{status_icon} {tool_name} Result[/{status_color}] ",
            title_align="left",
            border_style=border_color,
            box=ROUNDED,
            padding=(0, 1),
        )

    res_lines = res_str.splitlines()
    total_lines = len(res_lines)

    max_display = 14
    if total_lines <= max_display:
        display_lines = res_lines
        footer_note = f"[dim]({total_lines} lines{time_str})[/dim]"
    else:
        display_lines = (
            res_lines[:9]
            + [f"[dim]... ({total_lines - 12} more lines hidden) ...[/dim]"]
            + res_lines[-3:]
        )
        footer_note = f"[dim]({total_lines} total lines · showing 12{time_str})[/dim]"

    escaped_lines = []
    for l in display_lines:
        if l.startswith("[dim]..."):
            escaped_lines.append(l)
        else:
            if len(l) > 140:
                l = l[:137] + "..."
            escaped_lines.append(l.replace("[", "\\["))

    body = "\n".join(escaped_lines)
    header = f"[{status_color}]{status_icon} {tool_name} Output[/{status_color}] {footer_note}"

    return Panel(
        body,
        title=f" {header} ",
        title_align="left",
        border_style=border_color,
        box=ROUNDED,
        padding=(0, 1),
    )


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
        if len(cwd_str) > 42:
            cwd_str = "..." + cwd_str[-39:]

        skills_count = len(self.skill_mgr.list_skills())
        mcp_count = len(self.mcp.list_servers())

        banner_content = (
            f"[bold bright_cyan]⚡ ScopeForge[/bold bright_cyan] [bold white]v{__version__}[/] "
            f"[dim]· Autonomous Agent Harness & Coding Assistant[/dim]\n\n"
            f"[bold]Model:[/]    [bold white]{active_cfg.name}[/] [dim]({_display_model_id(active_cfg)})[/dim]\n"
            f"[bold]Mode:[/]     [bold {mode_color}][{self.sec_mode.upper()}][/] [dim](ScopeGate RoE sandbox)[/dim]\n"
            f"[bold]Scope:[/]    [bold yellow]{scopes_str}[/]\n"
            f"[bold]Dir:[/]      [dim]{cwd_str}[/]\n"
            f"[bold]Ready:[/]    [dim]{skills_count} skills discovered · {mcp_count} MCP servers · RAG vector store online[/dim]\n\n"
            f"[dim]Type [bold white]/help[/] for commands · [bold white]/mode[/] to switch · [bold white]/model[/] to change active LLM[/dim]"
        )

        self.console.print()
        self.console.print(
            Panel(
                banner_content,
                border_style="bright_blue",
                box=ROUNDED,
                padding=(1, 2),
            )
        )
        self.console.print()

    def get_prompt_text(self) -> HTML:
        """Generate sleek Claude Code-style enclosed input box outline."""
        import shutil

        cols = shutil.get_terminal_size((80, 20)).columns
        w = max(40, min(cols - 4, 88))

        mode_str = self.sec_mode.upper()
        mode_color = {
            "plan": "ansigreen",
            "audit": "ansicyan",
            "redteam": "ansiyellow",
            "blueteam": "ansiblue",
            "live": "ansired",
        }.get(self.sec_mode.lower(), "ansigreen")

        agent_tag = f" <ansimagenta>[{self.pending_agent}]</ansimagenta>" if self.pending_agent else ""
        tag_len = len(mode_str) + 4 + (len(self.pending_agent) + 3 if self.pending_agent else 0)
        bar_len = max(5, w - tag_len - 5)

        top_line = (
            f"<ansibrightblack>╭─ </ansibrightblack>"
            f"<{mode_color}><b>[{mode_str}]</b></{mode_color}>{agent_tag}"
            f"<ansibrightblack> {'─' * bar_len}╮</ansibrightblack>\n"
            f"<ansibrightblack>│ </ansibrightblack><ansibrightcyan><b>❯</b></ansibrightcyan> "
        )
        return HTML(top_line)

    async def execute_mission(self, prompt: str):
        """Execute a mission prompt through the multi-agent orchestrator with live streaming."""
        forced = self.pending_agent
        self.pending_agent = None

        if forced:
            self.console.print(
                f"[dim magenta]⇋ Supervisor routing directly to {forced.capitalize()}Agent[/dim magenta]"
            )

        start_time = time.time()
        tool_start_time = 0.0
        streamed_chars = 0
        current_tool: Optional[str] = None
        streaming_markup_suppressed = False
        suppression_buffer = ""
        live_markdown: Optional[Live] = None
        prose_buffer = ""

        # Tags that should NEVER leak to terminal stdout during text streaming
        SUPPRESS_TAG_PREFIXES = (
            "<dots_function_call",
            "<tool_call",
            "<function_call",
            "<function=",
            "<function ",
            "<function>",
            "<parameter=",
            "<parameter ",
            "<parameter>",
            "<invoke",
            "<|tool_call_start|>",
            "<arg_key",
            "<arg_value",
            "Invoking tool:",
            "invoking tool:",
            "Action: ",
            "Tool: ",
        )
        SUPPRESS_TAG_SUFFIXES = (
            "</dots_function_call>",
            "</tool_call>",
            "</function_call>",
            "</function>",
            "</parameter>",
            "</invoke>",
            "</arg_key>",
            "</arg_value>",
            "<|tool_call_end|>",
        )

        def _on_token(agent: str, chunk: str):
            nonlocal streamed_chars, current_tool, tool_start_time, streaming_markup_suppressed, suppression_buffer, live_markdown, prose_buffer
            if isinstance(chunk, str) and chunk.startswith('{"__type__":'):
                try:
                    data = json.loads(chunk)
                    evt_type = data.get("__type__")

                    if evt_type == "tool_call":
                        current_tool = data.get("name", "")
                        tool_start_time = time.time()
                        args = data.get("args", {})
                        if live_markdown is not None:
                            try:
                                live_markdown.stop()
                            except Exception:
                                pass
                            live_markdown = None
                            prose_buffer = ""
                        elif streamed_chars > 0:
                            sys.stdout.write("\n")
                            sys.stdout.flush()
                        self.console.print()
                        self.console.print(_format_tool_call_card(current_tool, args, agent))
                        return

                    elif evt_type == "tool_result":
                        if live_markdown is not None:
                            try:
                                live_markdown.stop()
                            except Exception:
                                pass
                            live_markdown = None
                            prose_buffer = ""
                        t_name = data.get("name", current_tool or "Tool")
                        res = data.get("result", "")
                        duration = time.time() - tool_start_time if tool_start_time > 0 else 0.0
                        self.console.print(_format_tool_result_card(t_name, str(res), duration=duration))
                        current_tool = None
                        return

                    elif evt_type == "a2a_banner":
                        if live_markdown is not None:
                            try:
                                live_markdown.stop()
                            except Exception:
                                pass
                            live_markdown = None
                            prose_buffer = ""
                        sender = data.get("sender", "Supervisor")
                        recipient = data.get("recipient", "Agent")
                        intent = data.get("intent", "TASK_DELEGATION")
                        prev = data.get("preview", "")
                        self.console.print()
                        self.console.print(
                            f"[dim magenta]─── ⇄ A2A Protocol: [bold]{sender}[/] → [bold]{recipient}[/] "
                            f"[{intent}] ───[/dim magenta]"
                        )
                        if prev:
                            self.console.print(f"  [dim]payload:[/] [dim white]{prev}[/]")
                        return

                except Exception:
                    pass

            # Detect and suppress model-hallucinated or raw XML tool tags from stdout
            if streaming_markup_suppressed:
                suppression_buffer += chunk
                for end_tag in SUPPRESS_TAG_SUFFIXES:
                    if end_tag in suppression_buffer:
                        end_pos = suppression_buffer.find(end_tag) + len(end_tag)
                        after_text = suppression_buffer[end_pos:]
                        streaming_markup_suppressed = False
                        suppression_buffer = ""
                        if after_text:
                            _on_token(agent, after_text)
                        return
                return

            for start_tag in SUPPRESS_TAG_PREFIXES:
                if start_tag in chunk:
                    idx = chunk.find(start_tag)
                    before_text = chunk[:idx]
                    if before_text:
                        streamed_chars += len(before_text)
                        if self.console.is_terminal:
                            prose_buffer += before_text
                            c_text = _clean_tool_call_text(prose_buffer)
                            if live_markdown is None:
                                live_markdown = Live(Markdown(c_text), console=self.console, refresh_per_second=15, transient=False)
                                live_markdown.start()
                            else:
                                live_markdown.update(Markdown(c_text))
                        else:
                            sys.stdout.write(before_text)
                            sys.stdout.flush()
                    streaming_markup_suppressed = True
                    suppression_buffer = chunk[idx:]
                    for end_tag in SUPPRESS_TAG_SUFFIXES:
                        if end_tag in suppression_buffer:
                            end_pos = suppression_buffer.find(end_tag) + len(end_tag)
                            after_text = suppression_buffer[end_pos:]
                            streaming_markup_suppressed = False
                            suppression_buffer = ""
                            if after_text:
                                _on_token(agent, after_text)
                            return
                    return

            # Stream clean LLM text chunk directly with rich Markdown rendering
            streamed_chars += len(chunk)
            if self.console.is_terminal:
                prose_buffer += chunk
                c_text = _clean_tool_call_text(prose_buffer)
                if live_markdown is None:
                    live_markdown = Live(Markdown(c_text), console=self.console, refresh_per_second=15, transient=False)
                    live_markdown.start()
                else:
                    live_markdown.update(Markdown(c_text))
            else:
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

            if live_markdown is not None:
                try:
                    live_markdown.stop()
                except Exception:
                    pass
                live_markdown = None
                prose_buffer = ""
            elif streamed_chars > 0:
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

            # Stats summary in modern status bar
            elapsed = time.time() - start_time
            turn_tokens = max(1, len(prompt) // 4) + 350
            self.total_tokens += turn_tokens
            turn_cost = 0.001 if "free" in self.provider_mgr.active_provider_name else 0.003
            self.estimated_cost += turn_cost

            mode_color = {
                "plan": "green",
                "audit": "cyan",
                "redteam": "yellow",
                "blueteam": "blue",
                "live": "red",
            }.get(self.sec_mode.lower(), "green")

            self.console.print()
            self.console.print(
                f"  [dim cyan]⚡[/] [bold white]{self.total_tokens:,}[/] [dim]tokens[/]  "
                f"[dim]·[/]  [bold green]${self.estimated_cost:.4f}[/]  "
                f"[dim]·[/]  [bold yellow]{elapsed:.2f}s[/]  "
                f"[dim]·[/]  [bold magenta]🤖 {active_agent.capitalize()}Agent[/]  "
                f"[dim]·[/]  [bold {mode_color}][{self.sec_mode.upper()}][/]"
            )
            self.console.print()

        except (KeyboardInterrupt, asyncio.CancelledError):
            self.console.print("\n[bold yellow]⚠️ Task interrupted by operator (<kbd>Ctrl+C</kbd>).[/bold yellow]\n")
            return
        except Exception as e:
            self.console.print(f"\n[bold red]❌ Multi-agent execution error: {e}[/bold red]\n")
        finally:
            if live_markdown is not None:
                try:
                    live_markdown.stop()
                except Exception:
                    pass

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
            if not args:
                self._show_scope()
            else:
                target = args[0].strip()
                gate = self.pipeline.get_scope_gate()
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
            self._show_cost()

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
        table = Table(
            title="[bold white]⚡ ScopeForge Interactive Command Reference[/bold white]",
            box=ROUNDED,
            border_style="bright_blue",
            show_header=True,
            header_style="bold bright_cyan",
        )
        table.add_column("Command", style="bold bright_cyan", width=22)
        table.add_column("Category", style="bold magenta", width=18)
        table.add_column("Description", style="white")

        # Category: Navigation & Execution
        table.add_row("/help", "Navigation", "Show this command reference")
        table.add_row("/mode [plan|audit|live]", "Execution RoE", "Switch ScopeGate sandbox (plan, audit, redteam, live)")
        table.add_row("/agent [name]", "Multi-Agent", "Route directly to recon, audit, exploit, dev")
        table.add_row("/clear", "Terminal", "Clear terminal screen and redraw status banner")
        table.add_row("/tui", "Terminal", "Switch to full-screen multi-pane Textual dashboard")
        table.add_row("/exit, /quit", "Session", "Exit ScopeForge session")

        # Category: Code & Workspace
        table.add_row("/diff", "Code & Git", "View color syntax git diff of workspace modifications")
        table.add_row("/commit <msg>", "Code & Git", "Stage and commit changes using git")
        table.add_row("/review", "Code & Git", "Autonomous security & quality code review of diff")
        table.add_row("/init", "Workspace", "Initialize SCOPEFORGE.md project instructions")

        # Category: Models & Configuration
        table.add_row("/model [name|#]", "Models", "Switch active LLM provider or model")
        table.add_row("/model add", "Models", "Register custom LLM profile (wizard or flags)")
        table.add_row("/config [set ...]", "Configuration", "View or update provider key, model, or base URL")
        table.add_row("/doctor", "Diagnostics", "Check environment, platform, models, and tools")

        # Category: Memory & Discovery
        table.add_row("/scope [target]", "ScopeGate", "Inspect or add authorized domain/IP targets")
        table.add_row("/compact", "Context", "Compact conversation context to preserve token budget")
        table.add_row("/cost", "Metrics", "Display session token usage and estimated cost")
        table.add_row("/wiki [query]", "SecWiki", "Search security wiki knowledge base")
        table.add_row("/rag [query]", "LlamaIndex", "Search LlamaIndex cybersecurity RAG store")

        self.console.print()
        self.console.print(table)
        self.console.print("[dim]Tip: Type any natural language prompt to begin autonomous execution.[/dim]\n")

    def _show_models(self):
        """Display all configured models in a clean, robust table."""
        providers = self.provider_mgr.list_providers()
        active_name = self.provider_mgr.active_provider_name

        try:
            table = Table(
                title=f"[bold white]⚡ ScopeForge LLM Providers & Models[/bold white] [dim]({len(providers)} configured)[/dim]",
                box=ROUNDED,
                border_style="bright_blue",
                show_lines=False,
                header_style="bold bright_cyan",
            )
            table.add_column("#", width=4, justify="right", style="bold cyan")
            table.add_column("Alias / Model", style="bold white", no_wrap=True)
            table.add_column("Provider", no_wrap=True)
            table.add_column("Model ID / Endpoint", style="white", overflow="ellipsis")
            table.add_column("Tier", no_wrap=True)
            table.add_column("Status", no_wrap=True)

            for idx, cfg in enumerate(providers):
                is_active = cfg.name == active_name
                status = "[bold green]● ACTIVE[/bold green]" if is_active else "[dim]○ ready[/dim]"
                prov_badge = _format_provider_badge(cfg.provider.value)
                tier = _infer_model_tier(cfg.name, cfg.model)
                prefix = "[bold green]★ [/bold green]" if is_active else "  "
                row_style = "bold white" if is_active else ""
                table.add_row(
                    str(idx + 1),
                    f"{prefix}{cfg.name}",
                    prov_badge,
                    _display_model_id(cfg),
                    tier,
                    status,
                    style=row_style,
                )

            self.console.print()
            self.console.print(table)
            self.console.print(
                "[dim]Switch: [bold white]/model <name or #>[/bold white]  ·  "
                "Add: [bold white]/model add[/bold white]  ·  "
                "Config: [bold white]/config[/bold white][/dim]\n"
            )
        except Exception:
            # Bulleted fallback in case terminal width or buffer encounters stream limits
            self.console.print(f"\n[bold]Configured LLM Models ({len(providers)}):[/]")
            for idx, cfg in enumerate(providers, 1):
                star = "★ " if cfg.name == active_name else "  "
                self.console.print(f" {star}{idx:2d}. [bold]{cfg.name}[/] ({_display_model_id(cfg)})")
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

                default_pt = "anthropic" if "claude" in (name + model_id).lower() else "custom"
                if not provider_type or provider_type == "custom":
                    pt_input = input(f"  2. Provider Type [custom, anthropic, openai, ollama, groq, openrouter] (default: {default_pt}): ").strip().lower()
                    provider_type = pt_input if pt_input else default_pt

                if not model_id:
                    model_id = input("  3. Model ID (e.g. claude-3-7-sonnet-20250219, gpt-4o, deepseek-chat): ").strip()
                    if not model_id:
                        self.console.print("[yellow]⚠️ Setup cancelled: Model ID is required.[/yellow]\n")
                        return

                if not base_url:
                    base_url = input("  4. Base URL (e.g. https://api.justwoker.icu, http://localhost:11434/v1, or press Enter): ").strip()

                if provider_type == "custom" and "claude" in model_id.lower() and not ("openrouter" in (base_url or "").lower() or "groq" in (base_url or "").lower() or "apmix.ai" in (base_url or "").lower()):
                    provider_type = "anthropic"

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

    def _show_scope(self):
        """Display ScopeGate authorization matrix table."""
        gate = self.pipeline.get_scope_gate()
        scopes = sorted(list(gate.authorized_scopes)) if gate else self.current_scope

        table = Table(
            title="[bold white]🛡️ ScopeGate Authorization Matrix[/bold white]",
            box=ROUNDED,
            border_style="yellow",
            show_header=True,
            header_style="bold yellow",
        )
        table.add_column("#", width=4, justify="right", style="bold cyan")
        table.add_column("Target Scope Pattern", style="bold bright_yellow", min_width=24)
        table.add_column("Pattern Type", style="cyan", min_width=18)
        table.add_column("Sandbox Mode", style="bold green", min_width=16)
        table.add_column("Enforcement", style="dim white", min_width=20)

        mode_str = f"[{self.sec_mode.upper()}]"
        for idx, s in enumerate(scopes, 1):
            if s.startswith("*."):
                p_type = "Wildcard Subdomain"
            elif s in ("localhost", "127.0.0.1", "::1"):
                p_type = "Local Loopback"
            elif "/" in s:
                p_type = "CIDR Subnet"
            else:
                p_type = "Exact Host / IP"

            table.add_row(
                str(idx),
                s,
                p_type,
                mode_str,
                "Strict ScopeGate RoE",
            )

        self.console.print()
        self.console.print(table)
        self.console.print("[dim]Add target: [bold white]/scope <domain or IP>[/]  ·  Switch mode: [bold white]/mode <name>[/][/dim]\n")

    def _show_cost(self):
        """Display session usage and cost metrics in a clean card."""
        active_cfg = self.provider_mgr.get_active_config()

        table = Table(
            title="[bold white]💰 Session Usage & Cost Analytics[/bold white]",
            box=ROUNDED,
            border_style="bright_blue",
            show_header=True,
            header_style="bold bright_cyan",
        )
        table.add_column("Metric", style="bold white", width=22)
        table.add_column("Value", style="bold bright_green", width=24)
        table.add_column("Notes", style="dim white")

        table.add_row("Total Session Tokens", f"{self.total_tokens:,}", "Estimated prompt + completion tokens")
        table.add_row("Estimated Cost", f"${self.estimated_cost:.4f}", "Based on provider token rates")
        table.add_row("Active Model", active_cfg.name, f"{active_cfg.provider.value} / {active_cfg.model}")
        table.add_row("Execution Mode", f"[{self.sec_mode.upper()}]", "ScopeGate sandbox policy")
        table.add_row("History Turns", f"{len(self.chat_history)} messages", "Conversation context window")

        self.console.print()
        self.console.print(table)
        self.console.print()

    def _show_doctor(self):
        """Run system diagnostics with a comprehensive diagnostics matrix table."""
        active_cfg = self.provider_mgr.get_active_config()
        skills = self.skill_mgr.list_skills()
        mcp_servers = self.mcp.list_servers()

        table = Table(
            title="[bold white]🏥 ScopeForge System Diagnostics (Doctor)[/bold white]",
            box=ROUNDED,
            border_style="bright_blue",
            show_header=True,
            header_style="bold bright_cyan",
        )
        table.add_column("Subsystem", style="bold bright_cyan", no_wrap=True)
        table.add_column("Component", style="bold white", no_wrap=True)
        table.add_column("Status", no_wrap=True)
        table.add_column("Diagnostics Details", style="dim white")

        # Python
        table.add_row(
            "Runtime",
            "Python Core",
            "[bold green]● OK[/]",
            f"v{platform.python_version()} ({sys.executable})",
        )
        # OS
        table.add_row(
            "Platform",
            platform.system(),
            "[bold green]● OK[/]",
            f"{platform.system()} {platform.release()} ({platform.machine()})",
        )
        # LLM
        prov_status = (
            "[bold green]● ONLINE[/]"
            if (active_cfg.api_key or active_cfg.provider == ProviderType.OLLAMA or "free" in active_cfg.name)
            else "[bold yellow]● NO KEY[/]"
        )
        table.add_row(
            "LLM Engine",
            active_cfg.name,
            prov_status,
            f"{active_cfg.provider.value} / {active_cfg.model} @ {active_cfg.api_base or 'default'}",
        )
        # ScopeGate
        table.add_row(
            "ScopeGate",
            f"Mode [{self.sec_mode.upper()}]",
            "[bold green]● ACTIVE[/]",
            f"{len(self.current_scope)} authorized targets: {', '.join(self.current_scope[:3])}",
        )
        # Agents
        table.add_row(
            "Agent Swarm",
            "Multi-Agent",
            "[bold green]● READY[/]",
            "Supervisor, DevAgent, ReconAgent, AuditAgent, ExploitAgent, ReportAgent",
        )
        # Skills
        table.add_row(
            "Extensibility",
            "Skills Manager",
            f"[bold green]● {len(skills)} READY[/]",
            f"{len(skills)} custom skills discovered across workspace",
        )
        # MCP
        table.add_row(
            "Protocols",
            "MCP Bridge",
            f"[bold green]● {len(mcp_servers)} CONNECTED[/]",
            f"{len(mcp_servers)} MCP servers active via stdio / SSE",
        )
        # RAG
        table.add_row(
            "Memory",
            "LlamaSecRAG",
            "[bold green]● ONLINE[/]",
            f"{len(self.rag.documents)} cybersecurity knowledge chunks indexed",
        )

        self.console.print()
        self.console.print(table)
        self.console.print("[dim green]✓ All systems nominal and ready for operations.[/dim green]\n")

    async def run_repl(self):
        """Run the main interactive REPL loop."""
        self.print_banner()
        last_ctrl_c_time = 0.0

        while True:
            try:
                user_input = await self.session.prompt_async(self.get_prompt_text())
                last_ctrl_c_time = 0.0

                import shutil
                cols = shutil.get_terminal_size((80, 20)).columns
                w = max(40, min(cols - 4, 88))
                self.console.print(f"[dim bright_black]╰{'─' * (w + 1)}╯[/dim bright_black]")

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
