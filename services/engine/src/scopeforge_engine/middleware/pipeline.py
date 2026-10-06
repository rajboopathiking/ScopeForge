"""Middleware Pipeline for orchestrating interceptors."""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from .base import BaseMiddleware
from .scope_gate import ScopeGateMiddleware
from .audit import AuditLoggingMiddleware
from .redaction import RedactionMiddleware
from .approval import ApprovalGateMiddleware


class MiddlewarePipeline:
    """Manages ordered execution of middleware interceptors."""

    def __init__(self, middlewares: Optional[List[BaseMiddleware]] = None):
        self.middlewares: List[BaseMiddleware] = sorted(
            middlewares or [],
            key=lambda m: m.priority,
        )

    def add_middleware(self, middleware: BaseMiddleware):
        self.middlewares.append(middleware)
        self.middlewares.sort(key=lambda m: m.priority)

    def get_scope_gate(self) -> Optional[ScopeGateMiddleware]:
        """Retrieve the ScopeGate middleware instance if present."""
        for m in self.middlewares:
            if isinstance(m, ScopeGateMiddleware):
                return m
        return None

    def get_middleware(self, name_or_type: Any) -> Optional[BaseMiddleware]:
        """Retrieve a middleware instance by name, class name, or type."""
        for m in self.middlewares:
            if isinstance(name_or_type, str):
                if m.name == name_or_type or m.__class__.__name__ == name_or_type:
                    return m
            elif isinstance(name_or_type, type) and isinstance(m, name_or_type):
                return m
        return None

    def set_mode(self, mode: str):
        """Set the execution mode on the ScopeGate middleware."""
        gate = self.get_scope_gate()
        if gate:
            gate.set_mode(mode)

    def run_before_llm(self, messages: List[Any], metadata: Dict[str, Any]) -> List[Any]:
        current = messages
        for m in self.middlewares:
            if m.enabled:
                current = m.before_llm(current, metadata)
        return current

    def run_after_llm(self, response: Any, metadata: Dict[str, Any]) -> Any:
        current = response
        for m in self.middlewares:
            if m.enabled:
                current = m.after_llm(current, metadata)
        return current

    def run_before_tool(
        self,
        tool_name: str,
        tool_args: Dict[str, Any],
        metadata: Dict[str, Any],
    ) -> Tuple[bool, str, Dict[str, Any]]:
        current_args = tool_args
        for m in self.middlewares:
            if m.enabled:
                proceed, reason, new_args = m.before_tool(tool_name, current_args, metadata)
                if not proceed:
                    return False, f"[{m.name}] {reason}", current_args
                current_args = new_args
        return True, "", current_args

    def run_after_tool(
        self,
        tool_name: str,
        result: Any,
        metadata: Dict[str, Any],
    ) -> Any:
        current = result
        for m in reversed(self.middlewares):
            if m.enabled:
                current = m.after_tool(tool_name, current, metadata)
        return current

    def run_on_a2a_message(
        self,
        message: Dict[str, Any],
        metadata: Dict[str, Any],
    ) -> Dict[str, Any]:
        current = message
        for m in self.middlewares:
            if m.enabled:
                current = m.on_a2a_message(current, metadata)
        return current


def create_default_pipeline(
    mode: str = "plan",
    authorized_scopes: Optional[List[str]] = None,
) -> MiddlewarePipeline:
    """Create standard security-hardened middleware pipeline."""
    return MiddlewarePipeline([
        RedactionMiddleware(),
        ScopeGateMiddleware(authorized_scopes=authorized_scopes, mode=mode),
        ApprovalGateMiddleware(),
        AuditLoggingMiddleware(),
    ])
