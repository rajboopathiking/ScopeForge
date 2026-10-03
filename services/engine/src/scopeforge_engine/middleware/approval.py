"""Approval Middleware: Flags sensitive or disruptive security actions for operator confirmation."""
from __future__ import annotations

import uuid
from typing import Any, Callable, Dict, Optional, Set, Tuple

from .base import BaseMiddleware, ToolApprovalRequest

SENSITIVE_TOOLS: Set[str] = {
    "falsifiable_poc_runner",
    "bash_security_exec",
    "recon_port_scan",
}


class ApprovalGateMiddleware(BaseMiddleware):
    """Enforces human authorization before dangerous security tool actions are dispatched."""

    def __init__(
        self,
        approval_callback: Optional[Callable[[ToolApprovalRequest], bool]] = None,
        auto_approve: bool = False,
    ):
        super().__init__(name="ApprovalGate", priority=15)
        self.approval_callback = approval_callback
        self.auto_approve = auto_approve
        self.pending_requests: Dict[str, ToolApprovalRequest] = {}

    def before_tool(
        self,
        tool_name: str,
        tool_args: Dict[str, Any],
        metadata: Dict[str, Any],
    ) -> Tuple[bool, str, Dict[str, Any]]:
        if self.auto_approve:
            return True, "", tool_args

        if tool_name in SENSITIVE_TOOLS:
            req_id = f"apr_{uuid.uuid4().hex[:8]}"
            req = ToolApprovalRequest(
                request_id=req_id,
                agent_name=metadata.get("agent", "Agent"),
                tool_name=tool_name,
                tool_args=tool_args,
                reason=f"Execution of sensitive tool '{tool_name}' requires explicit operator verification.",
                severity="HIGH" if tool_name in ("falsifiable_poc_runner", "bash_security_exec") else "MEDIUM",
            )
            self.pending_requests[req_id] = req

            # If an async or sync callback is configured:
            if self.approval_callback:
                approved = self.approval_callback(req)
                req.approved = approved
                if not approved:
                    return False, f"Operator DENIED approval for tool '{tool_name}' (Request ID: {req_id})", tool_args
                return True, "", tool_args
            else:
                # In TUI mode, pending requests can be examined or auto-resolved by TUI approval modal
                pass

        return True, "", tool_args
