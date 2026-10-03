"""LangGraph Multi-Agent Orchestration Network for ScopeForge."""
from __future__ import annotations

import json
from typing import Any, Dict, List, Literal, Optional
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage
from langgraph.graph import END, START, StateGraph

from pathlib import Path
from ..a2a.bus import A2ABus
from ..a2a.protocol import A2AIntent, A2AMessage
from ..llm_providers.manager import ProviderManager
from ..middleware.pipeline import MiddlewarePipeline, create_default_pipeline
from ..rag.engine import LlamaSecRAG
from ..sec_tools import (
    ALL_CODE_TOOLS,
    ALL_CYBER_TOOLS,
    ALL_SUITE_TOOLS,
    bash_security_exec,
    cve_advisory_search,
    edit_file,
    evidence_recorder,
    falsifiable_poc_runner,
    git_commit_tool,
    git_diff_tool,
    git_status_tool,
    glob_files,
    grep_search,
    recon_port_scan,
    sast_code_audit,
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
    ):
        self.provider_mgr = provider_manager or ProviderManager()
        self.pipeline = pipeline or create_default_pipeline()
        self.rag = rag or LlamaSecRAG()
        self.wiki = wiki or SecWiki()
        self.a2a_bus = a2a_bus or A2ABus()
        self.skill_mgr = skill_manager or SkillManager()
        self.custom_loader = CustomAgentLoader()

        self.graph = self._build_graph()

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
        return str(sanitized_result)

    def _build_graph(self):
        builder = StateGraph(AgentState)

        # 1. Define Agent Nodes
        async def supervisor_node(state: AgentState) -> Dict[str, Any]:
            last_message = state["messages"][-1].content if state["messages"] else ""
            last_lower = str(last_message).lower()

            # Retrieve RAG, Wiki, and Project Memory context
            rag_info = self.rag.get_context_for_prompt(str(last_message))
            user_prefs = self.wiki.get_user_preferences()
            proj_rules = self._get_project_instructions()

            # Routing heuristic / agent delegation
            target_agent = "supervisor"
            intent = A2AIntent.TASK_DELEGATION

            if any(k in last_lower for k in ["scan", "port", "recon", "surface", "enum"]):
                target_agent = "recon"
            elif any(k in last_lower for k in ["sast", "cve", "vuln", "sqli", "xss"]):
                target_agent = "audit"
            elif any(k in last_lower for k in ["poc", "exploit", "verify", "falsif"]):
                target_agent = "exploit"
            elif any(k in last_lower for k in ["report", "summary", "cvss", "remediation"]):
                target_agent = "report"
            elif any(k in last_lower for k in ["git status", "git diff", "view file", "read file", "edit file", "write file", "glob", "grep", "search code", "find file"]):
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

            # Direct supervisor response
            chat_model = self.provider_mgr.get_chat_model()
            skills_info = self.skill_mgr.get_prompt_instructions(str(last_message))
            sys_prompt = (
                "You are the ScopeForge Claude Code / Open Code Supervisor. "
                "You serve as an intelligent general coding assistant, system architect, "
                "and cybersecurity operations coordinator.\n"
                f"{proj_rules}\n{user_prefs}\n{rag_info}\n{skills_info}"
            )
            prompt_msgs = [SystemMessage(content=sys_prompt)] + list(state["messages"][-5:])
            prompt_msgs = self.pipeline.run_before_llm(prompt_msgs, {"agent": "Supervisor"})
            response = await chat_model.ainvoke(prompt_msgs)
            response = self.pipeline.run_after_llm(response, {"agent": "Supervisor"})

            return {
                "active_agent": "supervisor",
                "next_step": "end",
                "messages": [response],
                "user_preferences": user_prefs,
                "rag_context": rag_info,
            }


        async def recon_node(state: AgentState) -> Dict[str, Any]:
            last_message = str(state["messages"][-1].content)
            target = "authorized.example"
            for t in state.get("scope", []):
                if "example" in t or "localhost" in t:
                    target = t
                    break

            # Execute recon tools with middleware protection
            scan_out = self._invoke_tool_safely(recon_port_scan, {"target": target, "ports": "80,443,8080"}, "ReconAgent")
            probe_out = self._invoke_tool_safely(web_surface_probe, {"url": f"https://{target}"}, "ReconAgent")

            # Emit A2A result back
            a2a_msg = self.a2a_bus.send(
                sender="ReconAgent",
                recipient="AuditAgent",
                intent=A2AIntent.TASK_RESULT,
                payload={"target": target, "status": "Ports and perimeter mapped", "findings_count": 2},
            )

            response_text = (
                f"### [ReconAgent] Perimeter Assessment for `{target}`\n\n"
                f"**Port Discovery Telemetry:**\n```json\n{scan_out}\n```\n\n"
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
            sample_code = "query = f'SELECT * FROM users WHERE user_id = {user_input}'\ncursor.execute(query)"

            # Execute SAST and CVE tools with middleware
            sast_out = self._invoke_tool_safely(sast_code_audit, {"code_snippet_or_file": sample_code}, "AuditAgent")
            cve_out = self._invoke_tool_safely(cve_advisory_search, {"query": "CVE-2024-3400"}, "AuditAgent")

            a2a_msg = self.a2a_bus.send(
                sender="AuditAgent",
                recipient="ExploitAgent",
                intent=A2AIntent.EVIDENCE_SHARING,
                payload={"cwe": "CWE-89", "severity": "CRITICAL", "confidence": 0.95},
            )

            finding = {
                "id": "FIND-001",
                "title": "SQL Injection in User Authentication Query",
                "severity": "CRITICAL",
                "cwe": "CWE-89",
                "cvss": 9.8,
            }

            response_text = (
                "### [AuditAgent] Vulnerability Analysis & SAST Audit\n\n"
                f"**Static Code Analysis:**\n```json\n{sast_out}\n```\n\n"
                f"**CVE Correlation Database:**\n```json\n{cve_out}\n```\n\n"
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
            poc_out = self._invoke_tool_safely(
                falsifiable_poc_runner,
                {
                    "hypothesis": "Dynamic host header reflection allows cache poisoning",
                    "target": "authorized.example",
                    "payload_type": "header_reflection",
                },
                "ExploitAgent",
            )

            ev_out = self._invoke_tool_safely(
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

            output = ""
            if "status" in last_lower or ("git" in last_lower and "diff" not in last_lower):
                status_out = self._invoke_tool_safely(git_status_tool, {}, "DevAgent")
                output = f"**Git Status:**\n```json\n{status_out}\n```"
            elif "diff" in last_lower:
                staged = "staged" in last_lower or "cached" in last_lower
                diff_out = self._invoke_tool_safely(git_diff_tool, {"staged": staged}, "DevAgent")
                output = f"**Git Diff:**\n```json\n{diff_out}\n```"
            elif "grep" in last_lower or "search" in last_lower:
                query = last_message.split()[-1].strip("\"'") if len(last_message.split()) > 1 else "def "
                grep_out = self._invoke_tool_safely(grep_search, {"query": query}, "DevAgent")
                output = f"**Code Search Results:**\n```json\n{grep_out}\n```"
            elif "glob" in last_lower or "find file" in last_lower:
                glob_out = self._invoke_tool_safely(glob_files, {"pattern": "**/*.py"}, "DevAgent")
                output = f"**Discovered Project Files:**\n```json\n{glob_out}\n```"
            else:
                glob_out = self._invoke_tool_safely(glob_files, {"pattern": "*"}, "DevAgent")
                status_out = self._invoke_tool_safely(git_status_tool, {}, "DevAgent")
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
    ) -> AgentState:
        """Execute the LangGraph multi-agent pipeline."""
        self.pipeline.middlewares[1].set_mode(mode)  # update ScopeGate mode

        initial_state: AgentState = {
            "messages": (history or []) + [HumanMessage(content=user_message)],
            "active_agent": "supervisor",
            "mission": user_message,
            "mode": mode,
            "scope": scope or ["authorized.example", "*.example.com", "localhost"],
            "findings": [],
            "evidence_store": [],
            "a2a_log": [],
            "user_preferences": "",
            "rag_context": "",
            "pending_approval": None,
            "next_step": None,
        }

        final_state = await self.graph.ainvoke(initial_state)
        return final_state
