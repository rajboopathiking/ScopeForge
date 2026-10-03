"""Audit Logging Middleware: Tamper-evident append-only log of all agent, tool, and A2A events."""
from __future__ import annotations

import datetime
import hashlib
import json
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from .base import BaseMiddleware


class AuditLoggingMiddleware(BaseMiddleware):
    """Logs all events to an append-only JSONL audit ledger with cryptographic hashes."""

    def __init__(self, log_path: Optional[Path] = None):
        super().__init__(name="AuditLogger", priority=20)
        self.log_path = log_path or Path(".scopeforge/audit.jsonl")
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        self._prev_hash = "0" * 64

    def _record_event(self, event_type: str, data: Dict[str, Any]):
        timestamp = datetime.datetime.now(datetime.timezone.utc).isoformat()
        payload = {
            "timestamp": timestamp,
            "event_type": event_type,
            "data": data,
            "prev_hash": self._prev_hash,
        }
        serialized = json.dumps(payload, sort_keys=True)
        entry_hash = hashlib.sha256(serialized.encode("utf-8")).hexdigest()
        payload["hash"] = entry_hash
        self._prev_hash = entry_hash

        with open(self.log_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(payload) + "\n")

    def before_llm(self, messages: List[Any], metadata: Dict[str, Any]) -> List[Any]:
        self._record_event("llm_invocation_start", {
            "message_count": len(messages),
            "agent": metadata.get("agent", "unknown"),
        })
        return messages

    def after_llm(self, response: Any, metadata: Dict[str, Any]) -> Any:
        content = getattr(response, "content", str(response))
        self._record_event("llm_invocation_complete", {
            "agent": metadata.get("agent", "unknown"),
            "content_length": len(str(content)),
        })
        return response

    def before_tool(
        self,
        tool_name: str,
        tool_args: Dict[str, Any],
        metadata: Dict[str, Any],
    ) -> Tuple[bool, str, Dict[str, Any]]:
        self._record_event("tool_dispatch", {
            "tool": tool_name,
            "args": tool_args,
            "agent": metadata.get("agent", "unknown"),
        })
        return True, "", tool_args

    def after_tool(self, tool_name: str, result: Any, metadata: Dict[str, Any]) -> Any:
        self._record_event("tool_completed", {
            "tool": tool_name,
            "result_summary": str(result)[:300],
            "agent": metadata.get("agent", "unknown"),
        })
        return result

    def on_a2a_message(self, message: Dict[str, Any], metadata: Dict[str, Any]) -> Dict[str, Any]:
        self._record_event("a2a_communication", message)
        return message
