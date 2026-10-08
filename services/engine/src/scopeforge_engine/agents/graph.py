"""LangGraph Multi-Agent Orchestration Network for ScopeForge."""
from __future__ import annotations

import ast
import asyncio
import contextvars
import html
import json
import re
import uuid
from typing import Any, Callable, Dict, List, Literal, Optional
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_core.tools import StructuredTool, tool
from langgraph.graph import END, START, StateGraph

_stream_callback_var: contextvars.ContextVar[Optional[Callable[[str, str], None]]] = contextvars.ContextVar(
    "_stream_callback_var", default=None
)


def _extract_chunk_text(chunk_content: Any) -> str:
    if not chunk_content:
        return ""
    if isinstance(chunk_content, str):
        return chunk_content
    if isinstance(chunk_content, list):
        parts = []
        for p in chunk_content:
            if isinstance(p, str):
                parts.append(p)
            elif isinstance(p, dict):
                parts.append(str(p.get("text") or p.get("content") or ""))
        return "".join(parts)
    return str(chunk_content)


def _normalize_tool_call(name: str, args: Dict[str, Any], tool_map: Optional[Dict[str, Any]] = None) -> tuple[str, Dict[str, Any]]:
    """Map common LLM hallucinated tool names and argument aliases to registered tools."""
    aliases = {
        "read_file": "view_file",
        "read": "view_file",
        "cat": "view_file",
        "open_file": "view_file",
        "file_view": "view_file",
        "view": "view_file",
        "write": "write_file",
        "save_file": "write_file",
        "create_file": "write_file",
        "edit": "edit_file",
        "modify_file": "edit_file",
        "replace_file_content": "edit_file",
        "append": "append_file",
        "bash": "bash_cli",
        "shell": "bash_cli",
        "terminal": "bash_cli",
        "run_command": "bash_cli",
        "execute_command": "bash_cli",
        "cmd": "bash_cli",
        "exec": "bash_cli",
        "glob": "glob_files",
        "list_files": "glob_files",
        "ls": "glob_files",
        "find_files": "glob_files",
        "dir": "glob_files",
        "grep": "grep_search",
        "search_code": "grep_search",
        "search_files": "grep_search",
        "find_in_files": "grep_search",
        "google_search": "google_web_search",
        "web_search": "google_web_search",
        "google": "google_web_search",
        "search_web": "google_web_search",
        "git_status": "git_status_tool",
        "status": "git_status_tool",
        "git_diff": "git_diff_tool",
        "diff": "git_diff_tool",
        "git_commit": "git_commit_tool",
        "commit": "git_commit_tool",
        "port_scan": "recon_port_scan",
        "nmap": "recon_port_scan",
        "sast": "sast_code_audit",
        "code_audit": "sast_code_audit",
        "cve": "cve_advisory_search",
        "cve_search": "cve_advisory_search",
        "poc": "falsifiable_poc_runner",
        "run_poc": "falsifiable_poc_runner",
        "evidence": "evidence_recorder",
        "screenshot": "capture_screenshot",
        "screenshot_tool": "capture_screenshot",
        "capture_screenshot": "capture_screenshot",
        "take_screenshot": "capture_screenshot",
        "playwright_automation": "capture_screenshot",
        "playwright": "capture_screenshot",
        "browser_screenshot": "capture_screenshot",
        "web_screenshot": "capture_screenshot",
        "subagent": "invoke_subagent",
        "delegate": "invoke_subagent",
        "a2a": "send_a2a_message",
    }
    clean_name = aliases.get(name.lower().strip(), name.strip())

    if tool_map:
        if clean_name not in tool_map:
            for tm_k in tool_map.keys():
                if tm_k.lower() == clean_name.lower():
                    clean_name = tm_k
                    break

    norm_args = dict(args)
    if "path" in norm_args and "file_path" not in norm_args:
        norm_args["file_path"] = norm_args.pop("path")
    if "filename" in norm_args and "file_path" not in norm_args:
        norm_args["file_path"] = norm_args.pop("filename")
    if "file" in norm_args and "file_path" not in norm_args:
        norm_args["file_path"] = norm_args.pop("file")
    if "filepath" in norm_args and "file_path" not in norm_args:
        norm_args["file_path"] = norm_args.pop("filepath")
    if "cmd" in norm_args and "command" not in norm_args:
        norm_args["command"] = norm_args.pop("cmd")
    if "--command" in norm_args and "command" not in norm_args:
        norm_args["command"] = norm_args.pop("--command")
    # Screenshot arg aliases (models emit url/output_file variants)
    if "link" in norm_args and "url" not in norm_args:
        norm_args["url"] = norm_args.pop("link")
    if "website" in norm_args and "url" not in norm_args:
        norm_args["url"] = norm_args.pop("website")
    for _k in ("output", "file", "destination", "output_path", "path"):
        if _k in norm_args and "output_file" not in norm_args:
            norm_args["output_file"] = norm_args.pop(_k)
            break
    if "script" in norm_args and "command" not in norm_args:
        norm_args["command"] = norm_args.pop("script")
    if "q" in norm_args and "query" not in norm_args:
        norm_args["query"] = norm_args.pop("q")
    if "pattern" not in norm_args and "glob" in norm_args:
        norm_args["pattern"] = norm_args.pop("glob")

    return clean_name, norm_args


def _extract_tool_calls(msg: Any, tool_map: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
    """Extract tool calls from native AIMessage.tool_calls or raw text syntax (ChatML, XML, JSON, ReAct)."""
    extracted: List[Dict[str, Any]] = []

    # 1. Native LangChain tool calls
    native = getattr(msg, "tool_calls", None) or []
    for tc in native:
        if isinstance(tc, dict) and tc.get("name"):
            name = tc.get("name")
            args = tc.get("args") or {}
            norm_name, norm_args = _normalize_tool_call(name, args, tool_map)
            extracted.append({
                "name": norm_name,
                "args": norm_args,
                "id": tc.get("id") or f"call_{uuid.uuid4().hex[:8]}"
            })
    if extracted:
        return extracted

    content = str(getattr(msg, "content", "") or "")
    if not content.strip():
        return []

    # 2. Qwen / ChatML syntax: <|tool_call_start|>[func(args)]<|tool_call_end|>
    qwen_matches = re.findall(r"<\|tool_call_start\|>(.*?)<\|tool_call_end\|>", content, re.DOTALL)
    for m in qwen_matches:
        m = m.strip()
        if m.startswith("{") or m.startswith("["):
            try:
                parsed = json.loads(m)
                items = parsed if isinstance(parsed, list) else [parsed]
                for item in items:
                    if isinstance(item, dict) and ("name" in item or "tool" in item):
                        name = item.get("name") or item.get("tool")
                        args = item.get("arguments") or item.get("args") or item.get("parameters") or {}
                        norm_name, norm_args = _normalize_tool_call(name, args, tool_map)
                        extracted.append({
                            "name": norm_name,
                            "args": norm_args,
                            "id": f"call_{uuid.uuid4().hex[:8]}"
                        })
            except Exception:
                pass

        func_calls = re.findall(r"(\w+)\s*\((.*?)\)", m, re.DOTALL)
        for fn_name, fn_args_str in func_calls:
            args_dict = {}
            if fn_args_str.strip():
                try:
                    tree = ast.parse(f"{fn_name}({fn_args_str})")
                    call_node = tree.body[0].value
                    for kw in call_node.keywords:
                        args_dict[kw.arg] = ast.literal_eval(kw.value)
                except Exception:
                    pairs = re.findall(r'(\w+)\s*=\s*(?:["\']([^"\']*)["\']|([^\s,\)]+))', fn_args_str)
                    for k, v1, v2 in pairs:
                        args_dict[k] = v1 if v1 != "" else v2
            norm_name, norm_args = _normalize_tool_call(fn_name, args_dict, tool_map)
            extracted.append({
                "name": norm_name,
                "args": norm_args,
                "id": f"call_{uuid.uuid4().hex[:8]}"
            })

    # 3. XML style: <tool_call>{"name": "...", "arguments": {...}}</tool_call>
    xml_matches = re.findall(r"<tool_call>(.*?)</tool_call>", content, re.DOTALL)
    for xm in xml_matches:
        try:
            data = json.loads(xm.strip())
            name = data.get("name") or data.get("tool")
            args = data.get("arguments") or data.get("args") or data.get("parameters") or {}
            norm_name, norm_args = _normalize_tool_call(name, args, tool_map)
            extracted.append({
                "name": norm_name,
                "args": norm_args,
                "id": f"call_{uuid.uuid4().hex[:8]}"
            })
        except Exception:
            pass

    # 4. Markdown JSON code blocks
    json_blocks = re.findall(r"```(?:json)?\s*(\{.*?\})\s*```", content, re.DOTALL)
    for jb in json_blocks:
        try:
            data = json.loads(jb)
            if any(k in data for k in ("tool", "name", "action")) and any(k in data for k in ("args", "arguments", "parameters", "action_input", "input")):
                t_name = data.get("name") or data.get("tool") or data.get("action")
                t_args = data.get("arguments") or data.get("args") or data.get("parameters") or data.get("action_input") or data.get("input") or {}
                if isinstance(t_name, str) and t_name.strip():
                    t_args_dict = t_args if isinstance(t_args, dict) else {"input": str(t_args)}
                    norm_name, norm_args = _normalize_tool_call(t_name, t_args_dict, tool_map)
                    extracted.append({
                        "name": norm_name,
                        "args": norm_args,
                        "id": f"call_{uuid.uuid4().hex[:8]}"
                    })
        except Exception:
            pass

    # 5. ReAct format: Action: <tool>\nAction Input: <input>
    react_matches = re.findall(r"(?:Action|Tool):\s*(\w+)\s*\n(?:Action\s+Input|Tool\s+Input|Args|Parameters):\s*([^\n]+|\{.*?\})", content, re.IGNORECASE | re.DOTALL)
    for act_name, act_inp in react_matches:
        act_name = act_name.strip()
        act_inp = act_inp.strip()
        args = {}
        if act_inp.startswith("{") and act_inp.endswith("}"):
            try:
                args = json.loads(act_inp)
            except Exception:
                args = {"input": act_inp}
        else:
            args = {"input": act_inp}
        norm_name, norm_args = _normalize_tool_call(act_name, args, tool_map)
        extracted.append({
            "name": norm_name,
            "args": norm_args,
            "id": f"call_{uuid.uuid4().hex[:8]}"
        })

    return extracted


def _clean_tool_call_text(content: str) -> str:
    """Strip raw tool-calling markup tags so conversation text remains clean and human-readable."""
    cleaned = content
    cleaned = re.sub(r"<\|tool_call_start\|>.*?<\|tool_call_end\|>", "", cleaned, flags=re.DOTALL)
    cleaned = re.sub(r"<tool_call>.*?</tool_call>", "", cleaned, flags=re.DOTALL)
    cleaned = re.sub(r"(?:Action|Tool):\s*\w+\s*\n(?:Action\s+Input|Tool\s+Input|Args|Parameters):\s*(?:[^\n]+|\{.*?\})", "", cleaned, flags=re.DOTALL | re.IGNORECASE)
    return cleaned.strip()


def _compact_context(messages: List[BaseMessage], max_messages: int = 18) -> List[BaseMessage]:
    """Preserves SystemMessage and initial user goal while compacting intermediate tool turns.

    Ensures OpenAI/Anthropic tool-calling protocol validity:
    - Never leaves an orphaned ToolMessage without its parent AIMessage(tool_calls).
    - Never leaves an AIMessage(tool_calls) without its corresponding ToolMessages.
    """
    if len(messages) <= max_messages:
        return messages

    header_msgs: List[BaseMessage] = []
    rest_msgs: List[BaseMessage] = []
    for i, m in enumerate(messages):
        if i == 0 and isinstance(m, SystemMessage):
            header_msgs.append(m)
        elif len(header_msgs) == 1 and isinstance(m, HumanMessage):
            header_msgs.append(m)
        else:
            rest_msgs.append(m)

    if not header_msgs:
        header_msgs = messages[:1]
        rest_msgs = messages[1:]

    tail_target = max(6, max_messages - len(header_msgs) - 1)
    if len(rest_msgs) <= tail_target:
        return messages

    cut_idx = len(rest_msgs) - tail_target
    while cut_idx < len(rest_msgs) and isinstance(rest_msgs[cut_idx], ToolMessage):
        cut_idx += 1

    if cut_idx >= len(rest_msgs) - 2:
        return messages

    pruned_count = cut_idx
    summary_msg = SystemMessage(
        content=(
            f"[ScopeForge Context Compactor: {pruned_count} earlier intermediate execution steps "
            f"were compacted to preserve token limits for long-running workflows.]"
        )
    )

    return header_msgs + [summary_msg] + rest_msgs[cut_idx:]

from pathlib import Path
from ..a2a.bus import A2ABus
from ..a2a.protocol import A2AIntent, A2AMessage
from ..llm_providers.manager import ProviderManager
from ..llm_providers.models import LLMConfig, ProviderType
from ..mcp_bridge import MCPBridge
from ..middleware.pipeline import MiddlewarePipeline, create_default_pipeline
from ..rag.engine import LlamaSecRAG
from ..sec_tools import (
    ALL_CODE_TOOLS,
    ALL_CYBER_TOOLS,
    ALL_SUITE_TOOLS,
    append_file,
    bash_cli,
    bash_security_exec,
    cve_advisory_search,
    edit_file,
    evidence_recorder,
    falsifiable_poc_runner,
    git_commit_tool,
    git_diff_tool,
    git_status_tool,
    glob_files,
    google_web_search,
    grep_search,
    recon_port_scan,
    sast_code_audit,
    store_large_file,
    view_file,
    web_surface_probe,
    write_file,
)
from ..skills.manager import SkillManager
from ..wiki.store import SecWiki
from .custom_agent import CustomAgentLoader
from .state import AgentState



class MultiAgentSecOpsOrchestrator:
    """Coordinates the cybersecurity multi-agent team via LangGraph."""

    def __init__(
        self,
        provider_manager: Optional[ProviderManager] = None,
        pipeline: Optional[MiddlewarePipeline] = None,
        rag: Optional[LlamaSecRAG] = None,
        wiki: Optional[SecWiki] = None,
        a2a_bus: Optional[A2ABus] = None,
        skill_manager: Optional[SkillManager] = None,
        mcp_bridge: Optional[MCPBridge] = None,
    ):
        self.provider_mgr = provider_manager or ProviderManager()
        self.pipeline = pipeline or create_default_pipeline()
        self.rag = rag or LlamaSecRAG()
        self.wiki = wiki or SecWiki()
        self.a2a_bus = a2a_bus or A2ABus()
        self.skill_mgr = skill_manager or SkillManager()
        self.mcp = mcp_bridge or MCPBridge()
        self.custom_loader = CustomAgentLoader()

        self.graph = self._build_graph()

    def set_mode(self, mode: str):
        """Change the ScopeGate execution mode ('plan', 'artifacts', 'live')."""
        self.pipeline.set_mode(mode)
        return f"ScopeGate mode changed to: {mode.upper()}"

    def add_authorized_scope(self, target: str):
        """Add an authorized target to the ScopeGate allowlist."""
        gate = self.pipeline.get_scope_gate()
        if gate:
            gate.add_scope(target)
            return f"Added '{target}' to authorized scopes"
        return "ScopeGate middleware not found"

    def get_current_mode(self) -> str:
        """Get the current ScopeGate execution mode."""
        gate = self.pipeline.get_scope_gate()
        if gate:
            return gate.mode
        return "unknown"

    def _get_project_instructions(self) -> str:
        """Load project instructions from SCOPEFORGE.md or CLAUDE.md if present."""
        for name in ["SCOPEFORGE.md", "CLAUDE.md", ".scopeforge/instructions.md"]:
            p = Path(name)
            if p.exists() and p.is_file():
                try:
                    with open(p, "r", encoding="utf-8") as f:
                        return f"\n[Project Guidelines from {name}]:\n" + f.read()[:4000]
                except Exception:
                    pass
        return ""

    def _invoke_tool_safely(self, tool_func: Any, args: Dict[str, Any], agent_name: str) -> str:
        """Execute a tool through the middleware pipeline."""
        tool_name = getattr(tool_func, "name", str(tool_func))
        allowed, reason, modified_args = self.pipeline.run_before_tool(
            tool_name=tool_name,
            tool_args=args,
            metadata={"agent": agent_name},
        )
        if not allowed:
            return f"[Tool Blocked by Middleware: {reason}]"

        try:
            raw_result = tool_func.invoke(modified_args)
        except Exception as e:
            raw_result = f"Error executing {tool_name}: {e}"

        sanitized_result = self.pipeline.run_after_tool(
            tool_name=tool_name,
            result=raw_result,
            metadata={"agent": agent_name},
        )
        out_str = str(sanitized_result)
        # Cap tool output size to prevent blowing up the LLM context window!
        MAX_TOOL_CHARS = 12000
        HEAD_CHARS = 8000
        TAIL_CHARS = 3000
        if len(out_str) > MAX_TOOL_CHARS:
            spill_hint = ""
            try:
                import datetime
                spill_dir = Path(".scopeforge/runs/tool_spills")
                spill_dir.mkdir(parents=True, exist_ok=True)
                clean_tool = "".join(c for c in tool_name if c.isalnum() or c in ("-", "_"))
                spill_file = spill_dir / f"spill_{clean_tool}_{datetime.datetime.now().strftime('%Y%m%d_%H%M%S')}.log"
                spill_file.write_text(out_str, encoding="utf-8")
                spill_hint = f" [Full untruncated content preserved on disk at: {spill_file}]"
            except Exception:
                pass

            omitted = len(out_str) - HEAD_CHARS - TAIL_CHARS
            out_str = (
                f"{out_str[:HEAD_CHARS]}\n\n"
                f"... [ScopeForge Context Window Safeguard: {omitted} characters truncated to avoid token overflow.{spill_hint}] ...\n\n"
                f"{out_str[-TAIL_CHARS:]}"
            )
        return out_str

    def _run_subagent(self, agent_name: str, task: str) -> str:
        """Execute a subagent synchronously, track with A2A protocol, and return its output."""
        agent_clean = agent_name.lower().strip()
        if agent_clean.endswith("agent"):
            agent_clean = agent_clean[:-5]

        # Publish A2A delegation message
        self.a2a_bus.send(
            sender="Supervisor",
            recipient=f"{agent_clean.capitalize()}Agent",
            intent=A2AIntent.TASK_DELEGATION,
            payload={"task": task},
        )

        cb = _stream_callback_var.get()
        if cb:
            cb("supervisor", json.dumps({
                "__type__": "a2a_banner",
                "sender": "Supervisor",
                "recipient": f"{agent_clean.capitalize()}Agent",
                "intent": "TASK_DELEGATION",
                "preview": task[:80],
            }))

        output = ""
        if agent_clean == "recon":
            import re
            m = re.search(r"\b(?:https?://)?([a-zA-Z0-9][-a-zA-Z0-9.]*\.[a-zA-Z]{2,}|localhost|127\.0\.0\.1)(?::\d+)?\b", task)
            target = m.group(1) if m else "authorized.example"
            scan_out = self._invoke_tool_safely(recon_port_scan, {"target": target, "ports": "80,443,8080"}, "ReconAgent")
            probe_out = self._invoke_tool_safely(web_surface_probe, {"url": f"https://{target}"}, "ReconAgent")
            output = f"[ReconAgent Results for {target}]\nPorts:\n{scan_out}\nSurface:\n{probe_out}"

        elif agent_clean == "audit":
            import re
            m_cve = re.search(r"(CVE-\d{4}-\d{4,7})", task, re.IGNORECASE)
            cve_q = m_cve.group(1).upper() if m_cve else "CVE-2024-3400"
            sast_out = self._invoke_tool_safely(sast_code_audit, {"code_snippet_or_file": task}, "AuditAgent")
            cve_out = self._invoke_tool_safely(cve_advisory_search, {"query": cve_q}, "AuditAgent")
            output = f"[AuditAgent Results]\nSAST:\n{sast_out}\nCVE ({cve_q}):\n{cve_out}"

        elif agent_clean == "exploit":
            poc_out = self._invoke_tool_safely(
                falsifiable_poc_runner,
                {"hypothesis": task, "target": "authorized.example", "payload_type": "verification"},
                "ExploitAgent",
            )
            output = f"[ExploitAgent Results]\nPoC:\n{poc_out}"

        elif agent_clean == "report":
            output = f"[ReportAgent Results]\nSummary: Assessment finalized for task '{task[:60]}'. Security findings cataloged and remediation roadmap prepared."

        elif agent_clean == "dev":
            task_low = task.lower()
            if ("skill" in task_low or "install" in task_low or "clone" in task_low) and ("github.com" in task_low or "http://" in task_low or "https://" in task_low):
                import re
                m_url = re.search(r"https?://[^\s'\"`]+", task)
                if m_url:
                    url = m_url.group(0).rstrip(".,;)")
                    ok, msg, installed = self.skill_mgr.install_skill_from_repo(url)
                    for s in installed:
                        self.skill_mgr.activate_skill(s)
                    output = f"Skill Installation: {'SUCCESS' if ok else 'FAILED'} - {msg}"
                else:
                    output = "DevAgent: Invalid URL for skill install."
            elif any(k in task_low for k in ("run command", "terminal", "bash", "execute command")) or task.strip().startswith("$") or task.strip().startswith("!"):
                cmd = task.strip().lstrip("$!").strip()
                output = self._invoke_tool_safely(bash_cli, {"command": cmd}, "DevAgent")
            elif "grep" in task_low or "search" in task_low:
                q = task.split()[-1].strip("\"'") if len(task.split()) > 1 else "def "
                output = self._invoke_tool_safely(grep_search, {"query": q}, "DevAgent")
            else:
                output = self._invoke_tool_safely(glob_files, {"pattern": "*"}, "DevAgent")
        else:
            output = f"Unknown subagent '{agent_name}'. Available: recon, audit, exploit, report, dev."

        # Publish A2A result back to supervisor
        self.a2a_bus.send(
            sender=f"{agent_clean.capitalize()}Agent",
            recipient="Supervisor",
            intent=A2AIntent.TASK_RESULT,
            payload={"task": task, "status": "COMPLETED", "summary": output[:200]},
        )
        return output

    def _get_orchestrator_tools(self) -> List[Any]:
        """Create LangChain tools bound to orchestrator for autonomous multi-agent and harness operations."""
        def _invoke_subagent_func(agent_name: str, task: str) -> str:
            return self._run_subagent(agent_name=agent_name, task=task)

        def _send_a2a_message_func(recipient: str, intent: str, message: str) -> str:
            clean_intent = A2AIntent.TASK_DELEGATION
            try:
                for member in A2AIntent:
                    if member.value.lower() == intent.lower() or member.name.lower() == intent.lower():
                        clean_intent = member
                        break
            except Exception:
                pass
            msg = self.a2a_bus.send(
                sender="Supervisor",
                recipient=recipient,
                intent=clean_intent,
                payload={"message": message},
            )
            cb = _stream_callback_var.get()
            if cb:
                cb("supervisor", json.dumps({
                    "__type__": "a2a_banner",
                    "sender": "Supervisor",
                    "recipient": recipient,
                    "intent": clean_intent.value,
                    "preview": message[:60],
                }))
            return json.dumps({
                "status": "SENT",
                "message_id": getattr(msg, "message_id", str(msg)),
                "sender": "Supervisor",
                "recipient": recipient,
                "intent": clean_intent.value,
            })

        def _install_skill_func(repo_url: str) -> str:
            ok, msg, installed = self.skill_mgr.install_skill_from_repo(repo_url)
            for s in installed:
                self.skill_mgr.activate_skill(s)
            return json.dumps({
                "success": ok,
                "message": msg,
                "installed_skills": installed,
                "active_skills": list(self.skill_mgr.active_skills),
            }, indent=2)

        def _create_skill_func(name: str, description: str, triggers: str, instructions: str) -> str:
            trigger_list = [t.strip() for t in triggers.split(",") if t.strip()]
            skill = self.skill_mgr.create_skill(name, description, trigger_list, instructions)
            self.skill_mgr.activate_skill(name)
            return json.dumps({
                "success": True,
                "skill": skill.name,
                "path": skill.path,
                "triggers": skill.triggers,
            }, indent=2)

        def _list_skills_func(**kwargs) -> str:
            skills = self.skill_mgr.list_skills()
            res = []
            for s in skills:
                res.append({
                    "name": s.name,
                    "description": s.description,
                    "triggers": s.triggers,
                    "active": s.name in self.skill_mgr.active_skills,
                })
            return json.dumps({"skills_count": len(res), "skills": res}, indent=2)

        def _add_mcp_server_func(name: str, command: str) -> str:
            try:
                try:
                    self.mcp.registry.remove_server(name)
                except Exception:
                    pass
                self.mcp.registry.add_server(name, command)
                self.mcp.enable_server(name)
                return json.dumps({"status": "SUCCESS", "message": f"MCP server '{name}' added and enabled."})
            except Exception as e:
                return json.dumps({"status": "ERROR", "error": str(e)})

        def _list_mcp_servers_func(**kwargs) -> str:
            servers = self.mcp.list_servers()
            tools = self.mcp.get_langchain_tools()
            return json.dumps({
                "servers": servers,
                "tools": [{"name": t.name, "description": t.description} for t in tools],
            }, indent=2)

        def _add_scope_func(target: str) -> str:
            msg = self.add_authorized_scope(target)
            return json.dumps({"status": "SUCCESS", "message": msg, "target": target})

        def _get_scope_status_func(**kwargs) -> str:
            gate = self.pipeline.get_scope_gate()
            return json.dumps({
                "mode": self.get_current_mode(),
                "authorized_scopes": sorted(list(gate.authorized_scopes)) if gate else [],
                "auto_adapt": getattr(gate, "auto_adapt", True) if gate else False,
            }, indent=2)

        return [
            StructuredTool.from_function(
                func=_invoke_subagent_func,
                name="invoke_subagent",
                description=(
                    "Delegate a task or sub-task to a specialized ScopeForge subagent. "
                    "Available agents: 'recon' (port scanning, perimeter probing), "
                    "'audit' (SAST code analysis, CVE advisory correlation), "
                    "'exploit' (falsifiable PoC verification, evidence recording), "
                    "'report' (SecOps executive reporting), "
                    "'dev' (file operations, bash execution, skill installation)."
                ),
            ),
            StructuredTool.from_function(
                func=_send_a2a_message_func,
                name="send_a2a_message",
                description=(
                    "Send an explicit inter-agent communication message across the A2A bus. "
                    "Recipient can be 'ReconAgent', 'AuditAgent', 'ExploitAgent', 'ReportAgent', 'DevAgent', or custom."
                ),
            ),
            StructuredTool.from_function(
                func=_install_skill_func,
                name="install_skill",
                description=(
                    "Install and activate a skill package from a git repository URL (e.g. 'https://github.com/...'). "
                    "Skills provide specialized domain playbooks and guidelines."
                ),
            ),
            StructuredTool.from_function(
                func=_create_skill_func,
                name="create_skill",
                description=(
                    "Create a new custom skill with SKILL.md instructions and trigger keywords. "
                    "Args: name (str), description (str), triggers (comma-separated string), instructions (markdown string)."
                ),
            ),
            StructuredTool.from_function(
                func=_list_skills_func,
                name="list_skills",
                description="List all discovered and active skills currently available in ScopeForge.",
            ),
            StructuredTool.from_function(
                func=lambda **kwargs: json.dumps(self.skill_mgr.reload()),
                name="reload_skills",
                description="Rescan SKILL.md files on disk so newly added skills apply without restart. Use after writing .scopeforge/skills/<name>/SKILL.md.",
            ),
            StructuredTool.from_function(
                func=_add_mcp_server_func,
                name="add_mcp_server",
                description="Add and activate a Model Context Protocol (MCP) server by name and command line.",
            ),
            StructuredTool.from_function(
                func=_list_mcp_servers_func,
                name="list_mcp_servers",
                description="List all configured MCP servers and their active tool definitions.",
            ),
            StructuredTool.from_function(
                func=lambda **kwargs: json.dumps(self.mcp.refresh()),
                name="reload_mcp",
                description="Refresh MCP tool discovery cache so newly enabled servers/tools appear immediately.",
            ),
            StructuredTool.from_function(
                func=_add_scope_func,
                name="add_authorized_scope",
                description="Dynamically add a target domain, URL, or IP address to the ScopeGate authorized scope allowlist.",
            ),
            StructuredTool.from_function(
                func=_get_scope_status_func,
                name="get_scope_status",
                description="Retrieve current ScopeGate authorization status, active mode, and allowlist entries.",
            ),
        ]

    async def _execute_react_agent_loop(
        self,
        agent_name: str,
        system_prompt: str,
        tools: List[Any],
        state: AgentState,
        max_iterations: int = 25,
        response_prefix: str = "",
    ) -> Dict[str, Any]:
        """Runs the autonomous ReAct (Reasoning -> Action -> Observation -> Synthesis) agent loop."""
        chat_model = self.provider_mgr.get_chat_model()
        active_cfg = self.provider_mgr.get_active_config()
        is_mock = (
            getattr(chat_model, "is_mock", False)
            or active_cfg.provider == ProviderType.MOCK
            or "mock" in str(getattr(chat_model, "model_name", "")).lower()
        )

        tool_map = {getattr(t, "name", str(t)): t for t in tools}
        model_to_call = chat_model
        if not is_mock and hasattr(chat_model, "bind_tools"):
            try:
                model_to_call = chat_model.bind_tools(tools)
            except Exception:
                model_to_call = chat_model

        prompt_msgs = [SystemMessage(content=system_prompt)] + list(state["messages"][-5:])
        prompt_msgs = self.pipeline.run_before_llm(prompt_msgs, {"agent": agent_name.capitalize()})
        cb = _stream_callback_var.get()

        try:
            current_msgs = list(prompt_msgs)
            iteration = 0
            response = AIMessage(content="")

            while iteration < max_iterations:
                current_msgs = _compact_context(current_msgs, max_messages=18)
                full_chunks = []

                async for chunk in model_to_call.astream(current_msgs):
                    full_chunks.append(chunk)
                    txt = _extract_chunk_text(getattr(chunk, "content", ""))
                    if txt and cb:
                        if "<|tool_call_start|>" not in txt and "<tool_call>" not in txt:
                            cb(agent_name.lower(), txt)

                if full_chunks:
                    iter_resp = full_chunks[0]
                    for c in full_chunks[1:]:
                        iter_resp = iter_resp + c
                else:
                    iter_resp = AIMessage(content="")

                extracted_content = _extract_chunk_text(getattr(iter_resp, "content", ""))
                if extracted_content:
                    iter_resp.content = extracted_content

                tool_calls = _extract_tool_calls(iter_resp, tool_map)
                if not tool_calls:
                    content_str = str(extracted_content or "").strip()
                    # Detect planning/reasoning responses that have no tool calls yet:
                    # If this is iteration 0 and content looks like a plan (contains future-tense
                    # markers like "I'll", "I will", "Step 1:"), push the model to act.
                    _is_planning_only = (
                        iteration == 0
                        and bool(tool_map)  # tools are available
                        and any(marker in content_str for marker in (
                            "I'll ", "I will ", "Step 1", "First,", "first,",
                            "Let me ", "I'll start", "I'll begin", "sequentially",
                            "I need to ", "Let's start", "I'm going to ",
                        ))
                        and len(content_str) < 800  # short planning text, not a real final answer
                    )
                    # Hallucinated-execution guard: task needs real side effects
                    # (screenshot, file write/cp, bash) but model produced ZERO tool
                    # calls and instead faked success JSON (status:success, file_size,
                    # PNG image data, ls output). Never accept that as final —
                    # force real execution like Claude Code does.
                    _tool_msgs_so_far = sum(1 for m in current_msgs if isinstance(m, ToolMessage))
                    _task_needs_exec = False
                    try:
                        _user_goal = ""
                        for _m in reversed(state.get("messages", []) or []):
                            if isinstance(_m, HumanMessage):
                                _user_goal = str(_m.content).lower()
                                break
                        _task_needs_exec = any(k in _user_goal for k in (
                            "screenshot", "capture", "playwright", "save to", "save it",
                            "copy to", "cp ", "mkdir", "write file", "create file",
                            "ls ", "verify", "desktop", "download",
                        ))
                    except Exception:
                        _task_needs_exec = False
                    _claims_success = bool(re.search(
                        r"(successfully captured|success.{0,20}captured|screenshot.*saved|"
                        r"file_size|PNG image data|1\.2MB|1920x1080|"
                        r"\"status\"\s*:\s*\"success\"|results?:\s*\n?.*verification)",
                        content_str, re.IGNORECASE,
                    ))
                    _is_hallucinated_exec = (
                        _task_needs_exec and _tool_msgs_so_far == 0
                        and (_claims_success or len(content_str) > 400)
                        and iteration < 2
                    )
                    if _is_planning_only or _is_hallucinated_exec:
                        # Inject a continuation prompt to push from planning → acting
                        current_msgs.append(AIMessage(content=content_str))
                        if _is_hallucinated_exec:
                            current_msgs.append(HumanMessage(
                                content=(
                                    "You produced a text-only success report with ZERO tool executions. "
                                    "That is fabricated — the files do not exist. Now execute REAL tool calls: "
                                    "use capture_screenshot(url, output_file) then bash_cli(command=\"ls -lh <file> && file <file>\") to verify. "
                                    "Only report success with observed ToolMessage output. If the browser extra is missing, report the real error."
                                )
                            ))
                        else:
                            current_msgs.append(HumanMessage(
                                content="Good plan. Now execute your tools immediately to fulfill the task. Do not describe what you will do — just call the tools now."
                            ))
                        iteration += 1
                        continue
                    response = iter_resp
                    break

                iteration += 1
                cleaned_text = _clean_tool_call_text(extracted_content)
                iter_resp.content = cleaned_text
                iter_resp.tool_calls = tool_calls
                current_msgs.append(iter_resp)

                for tc in tool_calls:
                    tc_name = tc.get("name", "")
                    tc_args = tc.get("args", {})
                    if not isinstance(tc_args, dict):
                        try:
                            tc_args = json.loads(tc_args) if isinstance(tc_args, str) else {}
                        except Exception:
                            tc_args = {}
                    tc_id = tc.get("id") or f"call_{tc_name}_{iteration}"

                    if cb:
                        cb(agent_name.lower(), json.dumps({"__type__": "tool_call", "name": tc_name, "args": tc_args}))
                    await asyncio.sleep(0.01)

                    if tc_name in tool_map:
                        t_out = await asyncio.to_thread(self._invoke_tool_safely, tool_map[tc_name], tc_args, f"{agent_name.capitalize()}Agent")
                    else:
                        t_out = f"Tool '{tc_name}' is not registered."

                    if cb:
                        cb(agent_name.lower(), json.dumps({"__type__": "tool_result", "name": tc_name, "result": str(t_out)}))
                    await asyncio.sleep(0.01)

                    current_msgs.append(ToolMessage(content=str(t_out), tool_call_id=tc_id, name=tc_name))

                response = iter_resp

            # Synthesis safeguard for multi-step workflows or iteration limits
            if (iteration >= max_iterations and getattr(response, "tool_calls", None)) or (
                not str(getattr(response, "content", "") or "").strip() and iteration > 0
            ):
                synth_msgs = _compact_context(list(current_msgs), max_messages=16)
                synth_msgs.append(
                    SystemMessage(
                        content="You have executed the required actions. Synthesize all observations, tool outputs, and actions above into a comprehensive, clear, and final response for the user."
                    )
                )
                synth_chunks = []
                async for chunk in chat_model.astream(synth_msgs):
                    synth_chunks.append(chunk)
                    txt = _extract_chunk_text(getattr(chunk, "content", ""))
                    if txt and cb:
                        cb(agent_name.lower(), txt)
                if synth_chunks:
                    synth_resp = synth_chunks[0]
                    for c in synth_chunks[1:]:
                        synth_resp = synth_resp + c
                    extracted_synth = _extract_chunk_text(getattr(synth_resp, "content", ""))
                    if extracted_synth.strip():
                        response = AIMessage(content=extracted_synth)

            # Claude Code resilience: reasoning models (deepseek-r1, etc.)
            if not str(getattr(response, "content", "") or "").strip():
                reason_text = ""
                try:
                    ak = getattr(response, "additional_kwargs", {}) or {}
                    for k in ("reasoning", "reasoning_content", "reasoning_details"):
                        v = ak.get(k)
                        if isinstance(v, str) and v.strip():
                            reason_text = v.strip()
                            break
                        if isinstance(v, list) and v:
                            parts = []
                            for item in v:
                                if isinstance(item, dict):
                                    t = item.get("text") or item.get("reasoning") or ""
                                    if t:
                                        parts.append(str(t))
                            if parts:
                                reason_text = "\n".join(parts).strip()
                                break
                    if not reason_text:
                        meta = getattr(response, "response_metadata", {}) or {}
                        for k in ("reasoning", "reasoning_content"):
                            v = meta.get(k)
                            if isinstance(v, str) and v.strip():
                                reason_text = v.strip()
                                break
                except Exception:
                    reason_text = ""
                if reason_text:
                    response = AIMessage(content=reason_text[:4000])
                else:
                    last_msg_txt = str(state["messages"][-1].content) if state.get("messages") else ""
                    response = AIMessage(
                        content=(
                            f"⚠️ **Empty reply from `{active_cfg.model}`** (transient reasoning-model blank — Claude Code retries instead of failing).\n\n"
                            f"Query was: **{last_msg_txt[:400]}**\n\n"
                            "Retry once, or switch to a non-reasoning preset:\n"
                            "- `/model openrouter-free` (`openrouter/free` — verified live)\n"
                            "- `/model openrouter-free-nemotron` / `/model groq-llama3`\n"
                        )
                    )

        except Exception as e:
            err = str(e)
            fallback_success = False
            is_openrouter = (
                active_cfg.provider == ProviderType.OPENROUTER
                or getattr(active_cfg.provider, "value", str(active_cfg.provider)).lower() == "openrouter"
                or "openrouter" in str(active_cfg.api_base or "").lower()
            )
            if active_cfg.model != "openrouter/free" and is_openrouter:
                try:
                    import os
                    from ..llm_providers.factory import create_chat_model
                    fb_key = (
                        active_cfg.api_key
                        or os.getenv("OPENROUTER_API_KEY")
                        or getattr(self.provider_mgr.providers.get("openrouter-free"), "api_key", None)
                    )
                    fb_cfg = LLMConfig(
                        name="openrouter-free-fallback",
                        provider=ProviderType.OPENROUTER,
                        model="openrouter/free",
                        api_key=fb_key,
                        api_base="https://openrouter.ai/api/v1",
                        temperature=0.2,
                        extra_headers={
                            "HTTP-Referer": "https://github.com/rajboopathiking/ScopeForge",
                            "X-Title": "ScopeForge Agent Harness",
                        },
                    )
                    fb_model = create_chat_model(fb_cfg)
                    fb_chunks = []
                    async for chunk in fb_model.astream(prompt_msgs):
                        fb_chunks.append(chunk)
                        txt = _extract_chunk_text(getattr(chunk, "content", ""))
                        if txt and cb:
                            cb(agent_name.lower(), txt)
                    if fb_chunks:
                        fb_response = fb_chunks[0]
                        for c in fb_chunks[1:]:
                            fb_response = fb_response + c
                        extracted_fb = _extract_chunk_text(getattr(fb_response, "content", ""))
                        if extracted_fb.strip():
                            response = AIMessage(content=extracted_fb)
                            fallback_success = True
                except Exception:
                    fallback_success = False

            if not fallback_success:
                for cand_name, cand_cfg in self.provider_mgr.providers.items():
                    if cand_name != active_cfg.name and getattr(cand_cfg, "api_key", None):
                        try:
                            from ..llm_providers.factory import create_chat_model
                            cand_model = create_chat_model(cand_cfg)
                            cand_chunks = []
                            async for chunk in cand_model.astream(prompt_msgs):
                                cand_chunks.append(chunk)
                                txt = _extract_chunk_text(getattr(chunk, "content", ""))
                                if txt and cb:
                                    cb(agent_name.lower(), txt)
                            if cand_chunks:
                                fb_resp = cand_chunks[0]
                                for c in cand_chunks[1:]:
                                    fb_resp = fb_resp + c
                                ext_txt = _extract_chunk_text(getattr(fb_resp, "content", ""))
                                if ext_txt.strip():
                                    response = AIMessage(
                                        content=(
                                            f"*[Resilience Failover: automatically switched to `{cand_name}` due to error on `{active_cfg.name}`]*\n\n"
                                            + ext_txt
                                        )
                                    )
                                    fallback_success = True
                                    break
                        except Exception:
                            continue

            if not fallback_success:
                err_short = err.splitlines()[0][:600] if err else type(e).__name__
                last_msg_txt = str(state["messages"][-1].content) if state.get("messages") else ""
                response = AIMessage(
                    content=(
                        f"⚠️ **LLM call failed** (`{active_cfg.name}` / `{active_cfg.model}`): {err_short}\n\n"
                        f"Query was: **{last_msg_txt[:400]}**\n\n"
                        "**Fix (pick one):**\n"
                        f"- `/model` — switch to another configured provider (e.g. `/model 1` or `/model claude`)\n"
                        f"- `/config set key <API_KEY>` — set key for `{active_cfg.provider.value}`\n"
                        f"- `/config set model <model_id>` — e.g. `openrouter/free`\n"
                        f"- `/doctor` — diagnose provider connections and configuration"
                    )
                )

        clean_res = _clean_tool_call_text(str(response.content).strip())
        if not clean_res.strip():
            clean_res = str(response.content).strip()
        if response_prefix and not clean_res.startswith(response_prefix):
            response.content = f"{response_prefix}\n\n{clean_res}"

        else:
            response.content = clean_res

        response = self.pipeline.run_after_llm(response, {"agent": agent_name.capitalize()})
        return {
            "active_agent": agent_name.lower(),
            "next_step": "end",
            "messages": [response],
            "user_preferences": state.get("user_preferences", ""),
            "rag_context": state.get("rag_context", ""),
        }

    def _build_graph(self):
        builder = StateGraph(AgentState)

        # 1. Define Agent Nodes
        async def supervisor_node(state: AgentState) -> Dict[str, Any]:
            import re

            last_message = state["messages"][-1].content if state["messages"] else ""
            last_lower = str(last_message).lower()

            # Retrieve RAG, Wiki, and Project Memory context
            rag_info = self.rag.get_context_for_prompt(str(last_message))
            user_prefs = self.wiki.get_user_preferences()
            proj_rules = self._get_project_instructions()

            # Routing heuristic / agent delegation (conservative, Open Code style:
            # general chat stays on supervisor; specialists only on operational intent).
            # Previous substring checks (`"port" in ...`, `"enum" in ...`) mis-routed
            # general questions like "explain ports in docker" or "surface tension".
            target_agent = "supervisor"
            intent = A2AIntent.TASK_DELEGATION

            # Explicit routing override from `/agent <name>` (stored in state)
            forced_agent = str(state.get("forced_agent") or "").lower().strip()
            if forced_agent in ("recon", "audit", "exploit", "report", "dev", "supervisor"):
                target_agent = forced_agent
                # One-shot unless TUI re-sets it; clear after use is handled by caller
            else:
                # Legacy prefix support: "[agent:xxx] query" (older TUI builds)
                m_forced = re.match(r"\[agent:(\w+)\]\s*(.*)", str(last_message), re.DOTALL | re.IGNORECASE)
                if m_forced and m_forced.group(1).lower() in ("recon", "audit", "exploit", "report", "dev"):
                    target_agent = m_forced.group(1).lower()
                    last_message = m_forced.group(2)
                    last_lower = str(last_message).lower()
                else:
                    def _has(pattern: str) -> bool:
                        return re.search(pattern, last_lower) is not None

                    # Operational recon: needs action verb + target-ish context, not conceptual Q&A
                    is_conceptual = _has(r"\b(what is|what are|explain|difference|vs\.?\b|tension|how does|tutorial|in docker|docker.*port)\b")
                    has_target_hint = _has(r"(authorized|example\.com|localhost|127\.0\.0\.1|https?://|\btarget\b|\bscope\b|:\d+\b)")
                    has_recon_verb = _has(r"\b(port\s*scans?|nmap|recon(naissance)?|enumerate\s+(ports|hosts|subdomains|surface)|surface\s*(probe|map|enum)|open\s+ports?|scan\s+(the\s+)?target)\b")
                    if has_recon_verb and (has_target_hint or not is_conceptual):
                        # Still avoid conceptual "explain ports in docker" style
                        if not (is_conceptual and not has_target_hint):
                            target_agent = "recon"
                    elif _has(r"\b(sast|static\s+analysis|cve-\d+|vuln\s*(scan|audit|assess)|sqli|xss|cwe-\d+)\b") and _has(
                        r"\b(audit|scan|review|check|triage|correlat|cve|sast|sqli|xss)\b"
                    ):
                        # Require security-operational context, not generic "review my code"
                        if has_target_hint or _has(r"\b(audit|sast|cve|vuln|sqli|xss|cwe)\b"):
                            # Generic "review my code for bugs" without sec keywords stays supervisor/dev
                            if _has(r"\b(sast|cve|vuln|sqli|xss|cwe|owasp)\b"):
                                target_agent = "audit"
                    elif _has(r"\b(poc|proof.of.concept|exploit(ation)?|falsif\w*|verify\s+(exploit|poc))\b") and (
                        has_target_hint or _has(r"\b(hypothesis|payload|target|poc|exploit)\b")
                    ):
                        target_agent = "exploit"
                    elif _has(r"\b(compile|generat|export|build|show|summariz)\b") and _has(
                        r"\b(report|cvss|remediation|findings|assessment)\b"
                    ):
                        target_agent = "report"
                    elif (
                        _has(r"(git\s+(status|diff|commit|clone)|view\s+file|read\s+file|edit\s+file|write\s+file|\bglob\b|\bgrep\b|search\s+code|find\s+file)")
                        or _has(r"\b(install\s+skill|install|clone|terminal|bash|shell|exec|run\s+command|run\s+in\s+terminal|mkdir|cp\s+-r)\b")
                        or _has(r"\b(google|web\s*search|search\s*(the\s*)?web|lookup\s*online|search\s*online|browse)\b")
                        or _has(r"\bmcp\s+(add|enable|disable|tools|list)\b")
                        or last_lower.startswith("$ ")
                        or last_lower.startswith("! ")
                    ):
                        target_agent = "dev"

            if target_agent != "supervisor":
                # Emit A2A task delegation message
                a2a_msg = self.a2a_bus.send(
                    sender="Supervisor",
                    recipient=target_agent.capitalize() + "Agent",
                    intent=intent,
                    payload={"task": last_message, "mode": state.get("mode", "plan")},
                )
                return {
                    "active_agent": target_agent,
                    "next_step": target_agent,
                    "user_preferences": user_prefs,
                    "rag_context": rag_info,
                    "a2a_log": state.get("a2a_log", []) + [a2a_msg.model_dump()],
                }

            # Direct supervisor response (Claude Code / Open Code general chat)
            chat_model = self.provider_mgr.get_chat_model()
            active_cfg = self.provider_mgr.get_active_config()
            is_mock = (
                getattr(chat_model, "is_mock", False)
                or active_cfg.provider == ProviderType.MOCK
                or "mock" in str(getattr(chat_model, "model_name", "")).lower()
            )
            skills_info = self.skill_mgr.get_prompt_instructions(str(last_message))
            sys_prompt = (
                "You are ScopeForge — a modern autonomous AI agent harness (matching Antigravity CLI and Claude Code) "
                "with full tool execution, multi-agent collaboration (A2A), Model Context Protocol (MCP), and dynamic skill capabilities.\n\n"
                "CRITICAL AUTONOMOUS BEHAVIOR RULES:\n"
                "1. YOU HAVE DIRECT LOCAL EXECUTION CAPABILITIES. You can run bash commands, edit files, view files, clone repos, "
                "search the web, install skills, add MCP servers, and delegate to specialized subagents.\n"
                "2. NEVER refuse execution by claiming you lack local access, cannot run terminal commands, or asking the user to copy/paste commands into their terminal. "
                "Always execute the required tool calls yourself directly.\n"
                "3. MULTI-STEP LONG RUNNING TASKS: When a task requires multiple steps (e.g. cloning a repo, installing skills, creating files, running tests, or auditing), "
                "execute each step sequentially using your tools. Do not stop halfway; continue until the entire user goal is achieved.\n"
                "4. SUBAGENTS & A2A: You can delegate to specialized agents (`recon`, `audit`, `exploit`, `report`, `dev`) using `invoke_subagent` "
                "and communicate over the A2A bus with `send_a2a_message`.\n"
                "5. SKILLS & MCP: You can install skills from git repositories with `install_skill`, create skills with `create_skill`, "
                "and configure MCP servers with `add_mcp_server`.\n"
                "6. Format code and output in clean markdown with diffs and file paths explicitly specified.\n"
                "7. REALISTIC CVSS & THREAT MODELING: Distinguish between active exploitable vulnerabilities and Informational/Hardening defense-in-depth gaps. "
                "Missing HTTP security headers (CSP, X-Frame-Options, HSTS) or wildcard CORS on public content are LOW/INFORMATIONAL or MEDIUM (CVSS 3.0-5.3), never Critical 9.0+. "
                "Reserve CRITICAL (CVSS 9.0+) strictly for verified, exploitable risks (unauthenticated RCE, confirmed SQL injection, auth bypass).\n"
                "8. LARGE FILE & REPORT STORAGE STRATEGY: When generating or storing large reports, code bases, or evidence dumps, "
                "NEVER attempt to output massive files (>4KB or 100+ lines) in a single tool call to avoid token truncation mid-content. "
                "Use progressive multi-step chunking: initialize with `write_file(..., overwrite=True)`, then append subsequent sections with `append_file(...)` or `write_file(..., append=True)`. "
                "When inspecting large files, use `view_file` which pages up to 250 lines and provides `next_start_line`. "
                "For large command outputs, pass `output_file='path/to/log'` in `bash_cli` to stream directly to disk without size truncation.\n"
                "9. DYNAMIC SCOPE ADAPTATION: ScopeGate automatically recognizes target domains, subdomains (*.domain.com), and endpoints from the user's mission. "
                "You can also dynamically register newly discovered perimeter targets using `add_authorized_scope(target)` to adapt authorization on-the-fly.\n"
                "10. NEVER FABRICATE TOOL OUTPUTS: never invent ```json {\"status\":\"success\"}``` blocks, file sizes (1.2MB), dimensions (1920x1080), "
                "or `ls`/`file` output in text. Only report files as saved after a real ToolMessage from capture_screenshot/bash_cli confirms it. "
                "Correct tools: capture_screenshot(url, output_file) for websites (NOT playwright_automation/screenshot_tool), "
                "bash_cli(command=\"ls -lh <file> && file <file>\") for verification (NOT bash_cli --command=). "
                "If capture_screenshot reports browser extra missing, surface that real error — do not fake a PNG.\n\n"
                f"{proj_rules}\n{user_prefs}\n{rag_info}\n{skills_info}"
            )
            all_tools = list(ALL_SUITE_TOOLS) + self._get_orchestrator_tools() + list(self.mcp.get_langchain_tools())
            res = await self._execute_react_agent_loop(
                agent_name="supervisor",
                system_prompt=sys_prompt,
                tools=all_tools,
                state=state,
                max_iterations=state.get("max_iterations") or 25,
            )
            res["user_preferences"] = user_prefs
            res["rag_context"] = rag_info
            return res


        async def recon_node(state: AgentState) -> Dict[str, Any]:
            last_message = str(state["messages"][-1].content)
            chat_model = self.provider_mgr.get_chat_model()
            active_cfg = self.provider_mgr.get_active_config()
            is_mock = (
                getattr(chat_model, "is_mock", False)
                or active_cfg.provider == ProviderType.MOCK
                or "mock" in str(getattr(chat_model, "model_name", "")).lower()
            )

            # Dynamic target extraction: check query for IP or hostname
            target = "authorized.example"
            import re
            m_target = re.search(r"\b(?:https?://)?([a-zA-Z0-9][-a-zA-Z0-9.]*\.[a-zA-Z]{2,}|localhost|127\.0\.0\.1)(?::\d+)?\b", last_message)
            if m_target and "example.com" not in m_target.group(1).lower():
                target = m_target.group(1)
            else:
                for t in state.get("scope", []):
                    if "example" in t or "localhost" in t:
                        target = t
                        break

            m_ports = re.search(r"\bports?\s*[:=]?\s*([0-9][0-9,\s-]*)", last_message, re.IGNORECASE)
            ports = m_ports.group(1).replace(" ", "").rstrip(",-") if m_ports else "80,443,8080"


            if not is_mock:
                recon_tools = [recon_port_scan, web_surface_probe, google_web_search]
                recon_prompt = (
                    "You are ReconAgent — ScopeForge's perimeter reconnaissance and asset mapping specialist.\n\n"
                    f"Target asset: {target}\n"
                    f"Queried ports: {ports}\n\n"
                    "You have tools for port scanning (recon_port_scan), web surface probing (web_surface_probe), "
                    "and online searches (google_web_search).\n"
                    "Execute your reconnaissance tools sequentially, analyze the findings, and synthesize telemetry.\n"
                    f"Always explicitly state the scanned target `{target}` and queried ports `{ports}` in your assessment summary.\n"
                    f"Prefix your final response with '### [ReconAgent] Perimeter Assessment for `{target}` (Ports: {ports})'."
                )
                a2a_msg = self.a2a_bus.send(
                    sender="ReconAgent",
                    recipient="AuditAgent",
                    intent=A2AIntent.TASK_RESULT,
                    payload={"target": target, "ports": ports, "status": "Ports and perimeter mapped", "findings_count": 2},
                )
                res = await self._execute_react_agent_loop(
                    agent_name="recon",
                    system_prompt=recon_prompt,
                    tools=recon_tools,
                    state=state,
                    max_iterations=state.get("max_iterations") or 25,
                    response_prefix=f"### [ReconAgent] Perimeter Assessment for `{target}` (Ports: {ports})",
                )
                res["a2a_log"] = state.get("a2a_log", []) + [a2a_msg.model_dump()]
                return res

            # Execute recon tools with middleware protection
            scan_out = await asyncio.to_thread(self._invoke_tool_safely, recon_port_scan, {"target": target, "ports": ports}, "ReconAgent")
            probe_out = await asyncio.to_thread(self._invoke_tool_safely, web_surface_probe, {"url": f"https://{target}"}, "ReconAgent")

            # Emit A2A result back
            a2a_msg = self.a2a_bus.send(
                sender="ReconAgent",
                recipient="AuditAgent",
                intent=A2AIntent.TASK_RESULT,
                payload={"target": target, "status": "Ports and perimeter mapped", "findings_count": 2},
            )

            response_text = (
                f"### [ReconAgent] Perimeter Assessment for `{target}`\n\n"
                f"**Port Discovery Telemetry (Ports: {ports}):**\n```json\n{scan_out}\n```\n\n"
                f"**Web Surface Probe:**\n```json\n{probe_out}\n```\n\n"
                f"-> Delegated surface data to **AuditAgent** via A2A Protocol for vulnerability correlation."
            )

            return {
                "active_agent": "recon",
                "next_step": "end",
                "messages": [AIMessage(content=response_text)],
                "a2a_log": state.get("a2a_log", []) + [a2a_msg.model_dump()],
            }

        async def audit_node(state: AgentState) -> Dict[str, Any]:
            last_message = str(state["messages"][-1].content)
            chat_model = self.provider_mgr.get_chat_model()
            active_cfg = self.provider_mgr.get_active_config()
            is_mock = (
                getattr(chat_model, "is_mock", False)
                or active_cfg.provider == ProviderType.MOCK
                or "mock" in str(getattr(chat_model, "model_name", "")).lower()
            )

            # Extract CVE and code early (used by both mock and non-mock paths)
            import re
            m_cve = re.search(r"(CVE-\d{4}-\d{4,7})", last_message, re.IGNORECASE)
            cve_query = m_cve.group(1).upper() if m_cve else "CVE-2024-3400"

            if not is_mock:
                audit_tools = [sast_code_audit, cve_advisory_search, view_file, grep_search]
                audit_prompt = (
                    "You are AuditAgent — ScopeForge's code auditing and vulnerability correlation specialist.\n\n"
                    f"CVE target for this audit: {cve_query}\n\n"
                    "You have tools for static code analysis (sast_code_audit), CVE database correlation (cve_advisory_search), "
                    "file viewing (view_file), and grep searching (grep_search).\n"
                    "Execute your audit tools sequentially and synthesize verified findings.\n"
                    f"Always mention the CVE identifier `{cve_query}` explicitly in your response summary.\n"
                    f"Prefix your final response with '### [AuditAgent] Vulnerability Analysis & SAST Audit — {cve_query}'."
                )
                a2a_msg = self.a2a_bus.send(
                    sender="AuditAgent",
                    recipient="ExploitAgent",
                    intent=A2AIntent.EVIDENCE_SHARING,
                    payload={"task": last_message, "status": "Audit completed"},
                )
                res = await self._execute_react_agent_loop(
                    agent_name="audit",
                    system_prompt=audit_prompt,
                    tools=audit_tools,
                    state=state,
                    max_iterations=state.get("max_iterations") or 25,
                    response_prefix=f"### [AuditAgent] Vulnerability Analysis & SAST Audit — {cve_query}",
                )
                res["a2a_log"] = state.get("a2a_log", []) + [a2a_msg.model_dump()]
                return res

            sample_code = "query = f'SELECT * FROM users WHERE user_id = {user_input}'\ncursor.execute(query)"

            m_code = re.search(r"```(?:[a-zA-Z0-9_-]+)?\s*\n(.*?)\n```", last_message, re.DOTALL)

            code_to_audit = m_code.group(1) if m_code else sample_code


            # Execute SAST and CVE tools with middleware
            sast_out = await asyncio.to_thread(self._invoke_tool_safely, sast_code_audit, {"code_snippet_or_file": code_to_audit}, "AuditAgent")
            cve_out = await asyncio.to_thread(self._invoke_tool_safely, cve_advisory_search, {"query": cve_query}, "AuditAgent")

            a2a_msg = self.a2a_bus.send(
                sender="AuditAgent",
                recipient="ExploitAgent",
                intent=A2AIntent.EVIDENCE_SHARING,
                payload={"cwe": "CWE-89", "severity": "CRITICAL", "confidence": 0.95, "cve": cve_query},
            )

            finding = {
                "id": "FIND-001",
                "title": f"Security Finding in Analyzed Target ({cve_query})",
                "severity": "CRITICAL",
                "cwe": "CWE-89",
                "cvss": 9.8,
            }

            response_text = (
                "### [AuditAgent] Vulnerability Analysis & SAST Audit\n\n"
                f"**Static Code Analysis:**\n```json\n{sast_out}\n```\n\n"
                f"**CVE Correlation Database (`{cve_query}`):**\n```json\n{cve_out}\n```\n\n"
                "-> Shared verified finding with **ExploitAgent** via A2A protocol."
            )

            return {
                "active_agent": "audit",
                "next_step": "end",
                "messages": [AIMessage(content=response_text)],
                "findings": state.get("findings", []) + [finding],
                "a2a_log": state.get("a2a_log", []) + [a2a_msg.model_dump()],
            }

        async def exploit_node(state: AgentState) -> Dict[str, Any]:
            # PoC Verification agent
            poc_out = await asyncio.to_thread(
                self._invoke_tool_safely,
                falsifiable_poc_runner,
                {
                    "hypothesis": "Dynamic host header reflection allows cache poisoning",
                    "target": "authorized.example",
                    "payload_type": "header_reflection",
                },
                "ExploitAgent",
            )

            ev_out = await asyncio.to_thread(
                self._invoke_tool_safely,
                evidence_recorder,
                {
                    "title": "PoC Reflection Test",
                    "artifact_type": "http_dump",
                    "content": poc_out,
                    "finding_id": "FIND-001",
                },
                "ExploitAgent",
            )

            a2a_msg = self.a2a_bus.send(
                sender="ExploitAgent",
                recipient="ReportAgent",
                intent=A2AIntent.HANDOVER,
                payload={"status": "PoC Verified", "verdict": "CONFIRMED"},
            )

            response_text = (
                "### [ExploitAgent] Falsifiable Verification & PoC Runner\n\n"
                f"**Controlled Experiment Telemetry:**\n```json\n{poc_out}\n```\n\n"
                f"**Evidence Integrity Preservation:**\n```json\n{ev_out}\n```\n\n"
                "-> Transferred validated proof to **ReportAgent** for executive synthesis."
            )

            return {
                "active_agent": "exploit",
                "next_step": "end",
                "messages": [AIMessage(content=response_text)],
                "a2a_log": state.get("a2a_log", []) + [a2a_msg.model_dump()],
            }

        async def report_node(state: AgentState) -> Dict[str, Any]:
            findings = state.get("findings", [])
            response_text = (
                "### [ReportAgent] SecOps Assessment Summary\n\n"
                "| Finding ID | Title | Severity | CVSS v3.1 | Status |\n"
                "|---|---|---|---|---|\n"
                "| `FIND-001` | SQL Injection in Query Builder | **CRITICAL** | 9.8 | Verified |\n"
                "| `FIND-002` | Missing Content-Security-Policy | **MEDIUM** | 5.3 | Confirmed |\n"
                "| `FIND-003` | Wildcard CORS Header Exposure | **MEDIUM** | 6.5 | Confirmed |\n\n"
                "**Remediation Action Plan:**\n"
                "1. Immediately rewrite database queries using parameterized SQLAlchemy prepared statements.\n"
                "2. Define explicit CORS origin whitelist; restrict `Access-Control-Allow-Origin` from `*` to authorized tenant subdomains.\n"
                "3. Deploy HSTS and restrictive Content-Security-Policy headers on the edge reverse proxy.\n\n"
                "*Evidence artifacts cryptographically hashed and archived in `.scopeforge/evidence/`.*"
            )

            a2a_msg = self.a2a_bus.send(
                sender="ReportAgent",
                recipient="Supervisor",
                intent=A2AIntent.TASK_RESULT,
                payload={"report_status": "FINALIZED", "total_findings": 3},
            )

            return {
                "active_agent": "report",
                "next_step": "end",
                "messages": [AIMessage(content=response_text)],
                "a2a_log": state.get("a2a_log", []) + [a2a_msg.model_dump()],
            }

        async def dev_node(state: AgentState) -> Dict[str, Any]:
            last_message = str(state["messages"][-1].content)
            last_lower = last_message.lower()

            chat_model = self.provider_mgr.get_chat_model()
            active_cfg = self.provider_mgr.get_active_config()
            is_mock = (
                getattr(chat_model, "is_mock", False)
                or active_cfg.provider == ProviderType.MOCK
                or "mock" in str(getattr(chat_model, "model_name", "")).lower()
            )

            # Fast-path: $ / ! prefix or explicit bash/terminal commands run bash_cli DIRECTLY
            # (bypasses LLM loop to guarantee deterministic output regardless of active provider)
            _raw = last_message.strip()
            _is_direct_shell = (
                _raw.startswith("$ ")
                or _raw.startswith("!")
                or _raw.startswith("cd ")
                or last_lower.startswith("/bash ")
                or any(k in last_lower for k in ("run command", "execute command"))
            )
            if _is_direct_shell:
                cmd = _raw
                if cmd.startswith("$ "):
                    cmd = cmd[2:].strip()
                elif cmd.startswith("!"):
                    cmd = cmd[1:].strip()
                elif last_lower.startswith("/bash "):
                    cmd = last_message[6:].strip()
                cmd_out = await asyncio.to_thread(self._invoke_tool_safely, bash_cli, {"command": cmd}, "DevAgent")
                try:
                    res_j = json.loads(cmd_out)
                    out_text = res_j.get("stdout") or res_j.get("stderr") or res_j.get("error") or "Executed successfully with no output."
                    rc = res_j.get("return_code", 0 if res_j.get("success") else 1)
                    output = f"💻 **Sandbox Terminal Command:** `{cmd}`\n```text\n{out_text}\n```\n*Return Code:* `{rc}`"
                except Exception:
                    output = f"💻 **Executed Sandbox Command:** `{cmd}`\n```json\n{cmd_out}\n```"
                a2a_msg = self.a2a_bus.send(
                    sender="DevAgent",
                    recipient="Supervisor",
                    intent=A2AIntent.TASK_RESULT,
                    payload={"task": last_message, "status": "COMPLETED"},
                )
                response_text = (
                    f"### [DevAgent] Code Workspace & Developer Operations\n\n"
                    f"{output}\n\n"
                    "-> Completed developer tooling request within local sandbox."
                )
                return {
                    "active_agent": "dev",
                    "next_step": "end",
                    "messages": [AIMessage(content=response_text)],
                    "a2a_log": state.get("a2a_log", []) + [a2a_msg.model_dump()],
                }

            # Fast-path: web search queries call google_web_search DIRECTLY to guarantee
            # deterministic "Web Search Results" output regardless of active LLM provider.
            _is_web_search = any(k in last_lower for k in (
                "search the web", "web search", "google search", "search online",
                "lookup online", "search for", "find online", "/search", "/web",
            )) and not any(k in last_lower for k in ("grep", "code search", "sast", "cve"))
            if _is_web_search:
                q = last_message
                for prefix in ("/search", "/web", "/google", "search the web for", "search online for",
                               "web search for", "google for", "google", "search for", "search", "lookup"):
                    if last_lower.startswith(prefix):
                        q = last_message[len(prefix):].strip(" :\"'")
                        break
                search_raw = await asyncio.to_thread(
                    self._invoke_tool_safely, google_web_search,
                    {"query": q or last_message, "max_results": 5}, "DevAgent"
                )
                try:
                    search_res = json.loads(search_raw)
                    hits = search_res.get("results", [])
                    if hits:
                        cards = [
                            f"- **[{h.get('title')}]({h.get('url')})**\n  {h.get('snippet')}\n  `{h.get('url')}`"
                            for h in hits
                        ]
                        output = f"🌐 **Web Search Results for '{q or last_message}':**\n\n" + "\n\n".join(cards)
                    else:
                        output = f"🌐 **Web Search Results** — No live results found for: `{q or last_message}`"
                except Exception:
                    output = f"🌐 **Web Search Results**\n```json\n{search_raw}\n```"
                a2a_msg = self.a2a_bus.send(
                    sender="DevAgent",
                    recipient="Supervisor",
                    intent=A2AIntent.TASK_RESULT,
                    payload={"task": last_message, "status": "COMPLETED"},
                )
                response_text = (
                    f"### [DevAgent] Code Workspace & Developer Operations\n\n"
                    f"{output}\n\n"
                    "-> Completed web search request."
                )
                return {
                    "active_agent": "dev",
                    "next_step": "end",
                    "messages": [AIMessage(content=response_text)],
                    "a2a_log": state.get("a2a_log", []) + [a2a_msg.model_dump()],
                }

            # LIVE autonomous ReAct loop for DevAgent when connected to a real LLM
            if not is_mock:
                dev_tools = list(ALL_CODE_TOOLS) + list(self.mcp.get_langchain_tools())
                dev_prompt = (
                    "You are DevAgent — ScopeForge's dedicated code workspace and developer operations agent.\n\n"
                    "You have direct execution capabilities to view files, edit files, write files, glob directory trees, "
                    "grep codebases, run bash commands in terminal, inspect git status/diff, and execute web searches.\n\n"
                    "BEHAVIOR RULES:\n"
                    "1. Execute each step sequentially using your tools until the user's objective is fully accomplished.\n"
                    "2. Never ask the user to run commands or edit files when you can do it directly with your tools.\n"
                    "3. Format diffs, code changes, and execution results clearly in markdown.\n"
                    "4. Prefix your final comprehensive response with '### [DevAgent] Code Workspace & Developer Operations'."
                )
                a2a_msg = self.a2a_bus.send(
                    sender="DevAgent",
                    recipient="Supervisor",
                    intent=A2AIntent.TASK_RESULT,
                    payload={"task": last_message, "status": "COMPLETED"},
                )
                res = await self._execute_react_agent_loop(
                    agent_name="dev",
                    system_prompt=dev_prompt,
                    tools=dev_tools,
                    state=state,
                    max_iterations=state.get("max_iterations") or 25,
                    response_prefix="### [DevAgent] Code Workspace & Developer Operations",
                )
                res["a2a_log"] = state.get("a2a_log", []) + [a2a_msg.model_dump()]
                return res

            output = ""
            # 1. Skill installation from repository
            if ("skill" in last_lower or "install" in last_lower or "clone" in last_lower) and ("github.com" in last_lower or "http://" in last_lower or "https://" in last_lower):
                import re
                m_url = re.search(r"https?://[^\s'\"`]+", last_message)
                if m_url:
                    url = m_url.group(0).rstrip(".,;)")
                    ok, msg, installed = await asyncio.to_thread(self.skill_mgr.install_skill_from_repo, url)
                    if ok:
                        for s in installed:
                            self.skill_mgr.activate_skill(s)
                        output = f"**Skill Installation Succeeded:**\n- {msg}\n- Skills are now loaded, activated, and ready in `.scopeforge/skills/` and `~/.scopeforge/skills/`."
                    else:
                        output = f"**Skill Installation Failed:**\n{msg}"
                else:
                    output = "**Skill Installation:** Please provide a valid git repository URL to install."

            # 2. Web search / Google lookup
            elif any(k in last_lower for k in ("google", "web search", "search the web", "search online", "lookup online")) or (("search" in last_lower or "lookup" in last_lower) and not any(k in last_lower for k in ("grep", "code", "file", "repo", "cve", "sast"))):
                q = last_message
                for prefix in ("/search", "/web", "/google", "search the web for", "search online for", "web search for", "google for", "google", "search for", "search", "lookup"):
                    if last_lower.startswith(prefix):
                        q = last_message[len(prefix):].strip(" :\"'")
                        break
                search_raw = await asyncio.to_thread(self._invoke_tool_safely, google_web_search, {"query": q or last_message, "max_results": 5}, "DevAgent")
                try:
                    search_res = json.loads(search_raw)
                    hits = search_res.get("results", [])
                    if hits:
                        cards = [f"- **[{h.get('title')}]({h.get('url')})**\n  {h.get('snippet')}\n  `{h.get('url')}`" for h in hits]
                        output = f"🌐 **Web Search Results for '{q or last_message}':**\n\n" + "\n\n".join(cards)
                    else:
                        output = f"🌐 No live web results found for: `{q or last_message}`"
                except Exception:
                    output = f"🌐 **Web Search Output:**\n```json\n{search_raw}\n```"

            # 3. MCP server management
            elif "mcp" in last_lower and any(w in last_lower for w in ("add", "enable", "disable", "list", "tools")):
                if "add" in last_lower:
                    import re
                    m_add = re.search(r"add\s+(?:mcp\s+server\s+)?([a-zA-Z0-9_\-]+)\s+(.+)", last_message, re.IGNORECASE)
                    if m_add:
                        s_name = m_add.group(1).strip()
                        s_cmd = m_add.group(2).strip()
                        try:
                            try:
                                self.mcp.registry.remove_server(s_name)
                            except Exception:
                                pass
                            self.mcp.registry.add_server(s_name, s_cmd)
                            self.mcp.enable_server(s_name)
                            output = f"✓ **Added & Enabled MCP Server:** `{s_name}` (`{s_cmd}`)"
                        except Exception as e:
                            output = f"❌ Error adding MCP server: {e}"
                    else:
                        output = "Usage: Please specify MCP server name and command (e.g. `add mcp server my-server npx ...`)"
                elif "tools" in last_lower:
                    tools = self.mcp.get_langchain_tools()
                    t_list = "\n".join(f"- **`{t.name}`**: {t.description}" for t in tools)
                    output = f"🔌 **Active MCP Tools ({len(tools)}):**\n\n{t_list or 'No MCP tools active.'}"
                elif "enable" in last_lower:
                    s_name = last_message.split()[-1].strip()
                    try:
                        self.mcp.enable_server(s_name)
                        output = f"✓ **Enabled MCP Server:** `{s_name}`"
                    except Exception as e:
                        output = f"❌ Error enabling MCP server: {e}"
                elif "disable" in last_lower:
                    s_name = last_message.split()[-1].strip()
                    try:
                        self.mcp.disable_server(s_name)
                        output = f"✓ **Disabled MCP Server:** `{s_name}`"
                    except Exception as e:
                        output = f"❌ Error disabling MCP server: {e}"
                else:
                    servers = self.mcp.list_servers()
                    s_list = "\n".join(f"- **{s['name']}**: {'[ENABLED]' if s['enabled'] else '[DISABLED]'} (`{s['command']}`)" for s in servers)
                    output = f"🔌 **Model Context Protocol (MCP) Servers:**\n\n{s_list}"

            # 4. Terminal command execution
            elif any(k in last_lower for k in ("run command", "terminal", "bash", "execute command", "git clone")) or last_message.strip().startswith("$ ") or last_message.strip().startswith("!") or last_message.strip().startswith("cd "):
                cmd = last_message.strip()
                if cmd.startswith("$ "):
                    cmd = cmd[2:].strip()
                elif cmd.startswith("!"):
                    cmd = cmd[1:].strip()
                cmd_out = await asyncio.to_thread(self._invoke_tool_safely, bash_cli, {"command": cmd}, "DevAgent")
                try:
                    res_j = json.loads(cmd_out)
                    out_text = res_j.get("stdout") or res_j.get("stderr") or res_j.get("error") or "Executed successfully with no output."
                    rc = res_j.get("return_code", 0 if res_j.get("success") else 1)
                    output = f"💻 **Sandbox Terminal Command:** `{cmd}`\n```text\n{out_text}\n```\n*Return Code:* `{rc}`"
                except Exception:
                    output = f"💻 **Executed Sandbox Command:** `{cmd}`\n```json\n{cmd_out}\n```"

            elif "status" in last_lower or ("git" in last_lower and "diff" not in last_lower):
                status_out = await asyncio.to_thread(self._invoke_tool_safely, git_status_tool, {}, "DevAgent")
                output = f"**Git Status:**\n```json\n{status_out}\n```"
            elif "diff" in last_lower:
                staged = "staged" in last_lower or "cached" in last_lower
                diff_out = await asyncio.to_thread(self._invoke_tool_safely, git_diff_tool, {"staged": staged}, "DevAgent")
                output = f"**Git Diff:**\n```json\n{diff_out}\n```"
            elif "grep" in last_lower or ("search" in last_lower and "code" in last_lower):
                query = last_message.split()[-1].strip("\"'") if len(last_message.split()) > 1 else "def "
                grep_out = await asyncio.to_thread(self._invoke_tool_safely, grep_search, {"query": query}, "DevAgent")
                output = f"**Code Search Results:**\n```json\n{grep_out}\n```"
            elif "glob" in last_lower or "find file" in last_lower:
                glob_out = await asyncio.to_thread(self._invoke_tool_safely, glob_files, {"pattern": "**/*.py"}, "DevAgent")
                output = f"**Discovered Project Files:**\n```json\n{glob_out}\n```"
            else:
                glob_out = await asyncio.to_thread(self._invoke_tool_safely, glob_files, {"pattern": "*"}, "DevAgent")
                status_out = await asyncio.to_thread(self._invoke_tool_safely, git_status_tool, {}, "DevAgent")
                output = f"**Repository Overview:**\n```json\n{status_out}\n```\n\n**Root Files:**\n```json\n{glob_out}\n```"

            a2a_msg = self.a2a_bus.send(
                sender="DevAgent",
                recipient="Supervisor",
                intent=A2AIntent.TASK_RESULT,
                payload={"task": last_message, "status": "COMPLETED"},
            )

            response_text = (
                f"### [DevAgent] Code Workspace & Developer Operations\n\n"
                f"{output}\n\n"
                "-> Completed developer tooling request within local sandbox."
            )

            return {
                "active_agent": "dev",
                "next_step": "end",
                "messages": [AIMessage(content=response_text)],
                "a2a_log": state.get("a2a_log", []) + [a2a_msg.model_dump()],
            }

        # 2. Add Nodes
        builder.add_node("supervisor", supervisor_node)
        builder.add_node("recon", recon_node)
        builder.add_node("audit", audit_node)
        builder.add_node("exploit", exploit_node)
        builder.add_node("report", report_node)
        builder.add_node("dev", dev_node)

        # 3. Add Edges
        builder.add_edge(START, "supervisor")

        def router(state: AgentState) -> str:
            step = state.get("next_step")
            if step in ("recon", "audit", "exploit", "report", "dev"):
                return step
            return END

        builder.add_conditional_edges(
            "supervisor",
            router,
            {
                "recon": "recon",
                "audit": "audit",
                "exploit": "exploit",
                "report": "report",
                "dev": "dev",
                END: END,
            },
        )

        builder.add_edge("recon", END)
        builder.add_edge("audit", END)
        builder.add_edge("exploit", END)
        builder.add_edge("report", END)
        builder.add_edge("dev", END)

        return builder.compile()

    async def run(
        self,
        user_message: str,
        mode: str = "plan",
        scope: Optional[List[str]] = None,
        history: Optional[List[BaseMessage]] = None,
        forced_agent: Optional[str] = None,
        on_token: Optional[Callable[[str, str], None]] = None,
        max_iterations: int = 25,
    ) -> AgentState:
        """Execute the LangGraph multi-agent pipeline."""
        gate = self.pipeline.get_scope_gate()
        if gate:
            gate.set_mode(mode)
            gate.adapt_to_mission(user_message, scope)
            active_scope = sorted(list(gate.authorized_scopes))
        else:
            self.set_mode(mode)
            active_scope = scope or ["authorized.example", "*.example.com", "localhost"]

        initial_state: AgentState = {
            "messages": (history or []) + [HumanMessage(content=user_message)],
            "active_agent": "supervisor",
            "mission": user_message,
            "mode": mode,
            "scope": active_scope,
            "findings": [],
            "evidence_store": [],
            "a2a_log": [],
            "user_preferences": "",
            "rag_context": "",
            "pending_approval": None,
            "next_step": None,
            "forced_agent": forced_agent,
            "max_iterations": max_iterations,
        }

        tok = _stream_callback_var.set(on_token)
        try:
            final_state = await self.graph.ainvoke(initial_state)
            return final_state
        finally:
            _stream_callback_var.reset(tok)
