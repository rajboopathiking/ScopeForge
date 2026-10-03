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
        # Last human message is the real query; system prompt is messages[0].
        last_human = ""
        for m in reversed(messages):
            if isinstance(m, HumanMessage):
                last_human = str(m.content)
                break
        if not last_human and messages:
            last_human = str(messages[-1].content)
        last_msg_lower = last_human.lower()
        is_fallback = str(self.model_name).startswith("[MOCK fallback")

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

        def _fallback_prefix() -> str:
            if not is_fallback:
                return ""
            # Surface the real config problem instead of silently degrading.
            # `model_name` carries e.g. "[MOCK fallback: missing OPENROUTER_API_KEY] ...".
            return (
                f"> ⚠️ **Offline mock active** — `{self.model_name}`\n"
                "> Connect a model for full reasoning: `/model`, "
                "`/config set key <KEY>`, `/config set model <id>`, `/doctor`.\n\n"
            )

        # --- Claude Code / Open Code style general coding answers (offline) ---
        # Keep these deterministic so general chat works with zero keys.
        if "list comprehension" in last_msg_lower:
            return _fallback_prefix() + (
                "### Python List Comprehension\n\n"
                "`[expr for item in iterable if cond]` builds a new list in one expression.\n\n"
                "```python\n"
                "# squares of evens 0..9\n"
                "squares = [x * x for x in range(10) if x % 2 == 0]\n"
                "# -> [0, 4, 16, 36, 64]\n"
                "```\n\n"
                "**When to use:** simple transform + filter. "
                "For nested loops or side effects, prefer an explicit `for` for readability."
            )
        if "decorator" in last_msg_lower:
            return _fallback_prefix() + (
                "### Python Decorators (2-line version)\n\n"
                "A decorator wraps a function to add behaviour without changing it.\n\n"
                "```python\n"
                "from functools import wraps\n"
                "def log_calls(fn):\n"
                "    @wraps(fn)\n"
                "    def inner(*a, **k):\n"
                "        print(f'call {fn.__name__}'); return fn(*a, **k)\n"
                "    return inner\n"
                "```\n\n"
                f"Query: {last_human[:200]}"
            )
        if "tcp" in last_msg_lower and "udp" in last_msg_lower:
            return _fallback_prefix() + (
                "### TCP vs UDP\n\n"
                "- **TCP:** connection-oriented, reliable, ordered (ACKs, retransmits, flow control). "
                "Use for HTTP/HTTPS, SSH, files.\n"
                "- **UDP:** connectionless, best-effort, low overhead. "
                "Use for DNS, VoIP, streaming, gaming.\n\n"
                "Rule of thumb: correctness → TCP; lowest latency → UDP."
            )
        if "lru" in last_msg_lower or "ordereddict" in last_msg_lower or "thread-safe" in last_msg_lower:
            return _fallback_prefix() + (
                "### Thread-safe LRU (OrderedDict)\n\n"
                "```python\n"
                "from collections import OrderedDict\n"
                "import threading\n"
                "class LRU:\n"
                "    def __init__(self, cap=128):\n"
                "        self.cap, self.d, self.lock = cap, OrderedDict(), threading.Lock()\n"
                "    def get(self, k):\n"
                "        with self.lock:\n"
                "            if k not in self.d: return None\n"
                "            self.d.move_to_end(k); return self.d[k]\n"
                "    def put(self, k, v):\n"
                "        with self.lock:\n"
                "            self.d[k] = v; self.d.move_to_end(k)\n"
                "            if len(self.d) > self.cap: self.d.popitem(last=False)\n"
                "```"
            )
        if "event-driven" in last_msg_lower or "polling" in last_msg_lower:
            return _fallback_prefix() + (
                "### Event-driven vs Polling\n\n"
                "- **Event-driven:** push on change (webhooks, queues, watchers). "
                "Low latency, efficient, but needs delivery/ordering/retry design.\n"
                "- **Polling:** pull on interval. Simple, resilient, but higher latency + wasted calls.\n\n"
                "Prefer events for freshness/scale; polling for simplicity or no webhook support. "
                "Hybrid: poll as fallback with backoff + ETag/`If-Modified-Since`."
            )
        if "b-tree" in last_msg_lower or "lsm" in last_msg_lower or "btree" in last_msg_lower:
            return _fallback_prefix() + (
                "### B-Tree vs LSM-Tree\n\n"
                "- **B-Tree:** in-place updates, good reads + range scans, suffers write amplification on random writes.\n"
                "- **LSM-Tree:** buffered memtable + SSTables, excellent write throughput, reads may check multiple levels (bloom filters help).\n\n"
                "OLTP point lookups → B-Tree (Postgres/MySQL). Write-heavy ingest → LSM (RocksDB/Cassandra)."
            )
        if "nginx" in last_msg_lower and "websocket" in last_msg_lower:
            return _fallback_prefix() + (
                "### Nginx WebSocket Upgrade\n\n"
                "```nginx\n"
                "map $http_upgrade $connection_upgrade { default upgrade; '' close; }\n"
                "server {\n"
                "  location /ws/ {\n"
                "    proxy_pass http://app:8000;\n"
                "    proxy_http_version 1.1;\n"
                "    proxy_set_header Upgrade $http_upgrade;\n"
                "    proxy_set_header Connection $connection_upgrade;\n"
                "    proxy_read_timeout 86400s; proxy_send_timeout 86400s;\n"
                "  }\n"
                "}\n"
                "```\n\n"
                "Use `proxy_http_version 1.1` + Upgrade headers; raise timeouts for long-lived sockets."
            )

        if "scan" in last_msg_lower or "recon" in last_msg_lower:
            # Only claim recon when the query is operational, not conceptual
            # ("explain ports in docker" stays general; "scan target" goes recon).
            operational = any(
                w in last_msg_lower
                for w in ["scan ", "scan target", "port scan", "recon ", "enumerate", "authorized"]
            )
            if operational or "port" in last_msg_lower and "target" in last_msg_lower:
                return _fallback_prefix() + (
                    "### Reconnaissance & Surface Analysis\n\n"
                    "Initiating authorized perimeter audit against target assets:\n\n"
                    "- **Scope Validation:** Destination validated against authorized policy rules.\n"
                    "- **Discovered Services:**\n"
                    "  - `TCP 80` (HTTP) — nginx/1.24.0 (Security headers missing: `Content-Security-Policy`, `Permissions-Policy`)\n"
                    "  - `TCP 443` (HTTPS) — TLS 1.3 active, Certificate CN valid\n"
                    "  - `TCP 8080` (HTTP-Alt) — Internal management gateway exposed with Basic Authentication\n\n"
                    "**Proposed Action:** Dispatching `AuditAgent` to inspect the exposed `/api/v1/auth` endpoint for authentication bypass heuristics."
                )
        if "sast" in last_msg_lower or "sqli" in last_msg_lower or ("audit" in last_msg_lower and "code" in last_msg_lower):
            return _fallback_prefix() + (
                "### Static Application Security Testing (SAST) Report\n\n"
                "Audited code repository for OWASP Top 10 vulnerabilities:\n\n"
                "- **Finding [SF-SAST-01]:** Potential SQL Injection in `database/query_builder.py:42`\n"
                "  - **Pattern:** Dynamic format string string concatenation in raw SQL query.\n"
                "  - **CWE:** CWE-89 (Improper Neutralization of Special Elements in SQL Command)\n"
                "  - **Severity:** **CRITICAL (CVSS: 9.8)**\n"
                "  - **Remediation:** Replace with parameterized prepared statements via SQLAlchemy ORM."
            )
        if "cve" in last_msg_lower or "vuln" in last_msg_lower:
            return _fallback_prefix() + (
                "### Threat Intelligence & CVE Advisory\n\n"
                "Correlating asset software stack with the NVD/CVE repository:\n\n"
                "- **CVE-2024-3400** (GlobalProtect Command Injection) — CVSS 10.0 [CRITICAL]\n"
                "- **CVE-2023-4863** (WebP Heap Buffer Overflow) — CVSS 8.8 [HIGH]\n"
                "- **CVE-2023-38606** (Kernel Privilege Escalation) — CVSS 7.8 [HIGH]\n\n"
                "Cross-referencing patch levels indicates component `libwebp` requires immediate upgrading."
            )
        # Open Code / Claude Code style general fallback: echo + structure,
        # not a dead-end capabilities dump.
        return _fallback_prefix() + (
            "### Assistant (offline mock)\n\n"
            f"Query: **{last_human[:400]}**\n\n"
            "I am running without a live LLM (mock). Here is how to proceed, Claude Code style:\n\n"
            "1. **Understand:** restate the goal + constraints.\n"
            "2. **Plan:** smallest reproducable step first.\n"
            "3. **Change:** edit one file / run one command, then verify.\n\n"
            "```text\n"
            "# next action\n"
            "# 1) clarify expected input/output\n"
            "# 2) locate relevant file (glob/grep)\n"
            "# 3) make minimal diff + test\n"
            "```\n\n"
            "Connect a model for full reasoning (`/model`, `/config set key <KEY>`, `/doctor`). "
            "Security ops route to specialists via `/agent recon|audit|exploit|report`, "
            "knowledge via `/rag <query>`, scope via `/scope add <target>`."
        )
