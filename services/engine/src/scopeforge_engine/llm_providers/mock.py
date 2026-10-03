"""Offline / Demo Mock Chat Model for cybersecurity agent harness."""
from __future__ import annotations

import json
from typing import Any, AsyncIterator, Iterator, List, Optional
from langchain_core.callbacks.manager import (
    AsyncCallbackManagerForLLMRun,
    CallbackManagerForLLMRun,
)
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import (
    AIMessage,
    AIMessageChunk,
    BaseMessage,
    HumanMessage,
    ToolMessage,
)
from langchain_core.outputs import ChatGeneration, ChatGenerationChunk, ChatResult


class MockSecOpsChatModel(BaseChatModel):
    """High-fidelity mock chat model simulating cybersecurity specialized reasoning."""

    model_name: str = "mock-secops"
    streaming: bool = True

    @property
    def _llm_type(self) -> str:
        return "mock-secops-chat"

    def _generate(
        self,
        messages: List[BaseMessage],
        stop: Optional[List[str]] = None,
        run_manager: Optional[CallbackManagerForLLMRun] = None,
        **kwargs: Any,
    ) -> ChatResult:
        content = self._craft_response(messages)
        message = AIMessage(content=content)
        generation = ChatGeneration(message=message)
        return ChatResult(generations=[generation])

    async def _agenerate(
        self,
        messages: List[BaseMessage],
        stop: Optional[List[str]] = None,
        run_manager: Optional[AsyncCallbackManagerForLLMRun] = None,
        **kwargs: Any,
    ) -> ChatResult:
        content = self._craft_response(messages)
        message = AIMessage(content=content)
        generation = ChatGeneration(message=message)
        return ChatResult(generations=[generation])

    async def _astream(
        self,
        messages: List[BaseMessage],
        stop: Optional[List[str]] = None,
        run_manager: Optional[AsyncCallbackManagerForLLMRun] = None,
        **kwargs: Any,
    ) -> AsyncIterator[ChatGenerationChunk]:
        content = self._craft_response(messages)
        chunks = content.split(" ")
        for i, word in enumerate(chunks):
            token = word + (" " if i < len(chunks) - 1 else "")
            chunk = ChatGenerationChunk(message=AIMessageChunk(content=token))
            if run_manager:
                await run_manager.on_llm_new_token(token)
            yield chunk

    def _craft_response(self, messages: List[BaseMessage]) -> str:
        last_msg = messages[-1].content if messages else ""
        last_msg_lower = str(last_msg).lower()

        # Check if last message was a tool result
        if any(isinstance(m, ToolMessage) for m in messages[-2:]):
            return (
                "### Verification & Impact Analysis\n\n"
                "Based on the tool telemetry gathered within the authorized scope:\n\n"
                "1. **Observed Evidence:** The target endpoint confirmed anomalous header reflection without input sanitization.\n"
                "2. **Impact Assessment:** Severity classified as **HIGH (CVSS:3.1/AV:N/AC:L/PR:N/UI:N/S:U/C:H/I:N/A:N)**. Unauthorized access to internal microservice routing tokens is plausible.\n"
                "3. **Evidence Hash:** SHA256 recorded in the session evidence store.\n"
                "4. **Remediation Recommendation:** Implement strict input validation and enforce CORS origin allowlists."
            )

        if "scan" in last_msg_lower or "port" in last_msg_lower or "recon" in last_msg_lower:
            return (
                "### Reconnaissance & Surface Analysis\n\n"
                "Initiating authorized perimeter audit against target assets:\n\n"
                "- **Scope Validation:** Destination validated against authorized policy rules.\n"
                "- **Discovered Services:**\n"
                "  - `TCP 80` (HTTP) — nginx/1.24.0 (Security headers missing: `Content-Security-Policy`, `Permissions-Policy`)\n"
                "  - `TCP 443` (HTTPS) — TLS 1.3 active, Certificate CN valid\n"
                "  - `TCP 8080` (HTTP-Alt) — Internal management gateway exposed with Basic Authentication\n\n"
                "**Proposed Action:** Dispatching `AuditAgent` to inspect the exposed `/api/v1/auth` endpoint for authentication bypass heuristics."
            )
        elif "sast" in last_msg_lower or "code" in last_msg_lower or "audit" in last_msg_lower or "sqli" in last_msg_lower:
            return (
                "### Static Application Security Testing (SAST) Report\n\n"
                "Audited code repository for OWASP Top 10 vulnerabilities:\n\n"
                "- **Finding [SF-SAST-01]:** Potential SQL Injection in `database/query_builder.py:42`\n"
                "  - **Pattern:** Dynamic format string string concatenation in raw SQL query.\n"
                "  - **CWE:** CWE-89 (Improper Neutralization of Special Elements in SQL Command)\n"
                "  - **Severity:** **CRITICAL (CVSS: 9.8)**\n"
                "  - **Remediation:** Replace with parameterized prepared statements via SQLAlchemy ORM."
            )
        elif "cve" in last_msg_lower or "vuln" in last_msg_lower:
            return (
                "### Threat Intelligence & CVE Advisory\n\n"
                "Correlating asset software stack with the NVD/CVE repository:\n\n"
                "- **CVE-2024-3400** (GlobalProtect Command Injection) — CVSS 10.0 [CRITICAL]\n"
                "- **CVE-2023-4863** (WebP Heap Buffer Overflow) — CVSS 8.8 [HIGH]\n"
                "- **CVE-2023-38606** (Kernel Privilege Escalation) — CVSS 7.8 [HIGH]\n\n"
                "Cross-referencing patch levels indicates component `libwebp` requires immediate upgrading."
            )
        else:
            return (
                "### ScopeForge Security Agent\n\n"
                "I am your multi-agent cybersecurity copilot. I operate under strict Rules of Engagement with ScopeGate policy enforcement.\n\n"
                "**Available Capabilities:**\n"
                "- `/scope <target>`: Configure authorized targets and boundary CIDRs\n"
                "- `/mode <plan|artifacts|live>`: Toggle safety execution modes\n"
                "- `/agent <recon|audit|exploit|report>`: Switch active agent specialist\n"
                "- `/rag <query>`: Query cybersecurity knowledge base (OWASP, CVEs, playbooks)\n"
                "- `/wiki`: Review user preferences and memory wiki\n"
                "- `/mcp`: Inspect connected Model Context Protocol servers\n\n"
                "How would you like to proceed with the security assessment?"
            )
