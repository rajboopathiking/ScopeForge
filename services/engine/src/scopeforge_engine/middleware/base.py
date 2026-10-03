"""Base Middleware interface for ScopeForge agent harness."""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple
from pydantic import BaseModel, Field


class ToolApprovalRequest(BaseModel):
    """Encapsulates a tool invocation requiring human approval."""
    request_id: str
    agent_name: str
    tool_name: str
    tool_args: Dict[str, Any]
    reason: str
    severity: str = "HIGH"
    approved: Optional[bool] = None


class BaseMiddleware:
    """Abstract base class for ScopeForge middleware components."""

    def __init__(self, name: str, enabled: bool = True, priority: int = 100):
        self.name = name
        self.enabled = enabled
        self.priority = priority

    def before_llm(self, messages: List[Any], metadata: Dict[str, Any]) -> List[Any]:
        """Intercept or mutate messages before dispatching to the LLM."""
        return messages

    def after_llm(self, response: Any, metadata: Dict[str, Any]) -> Any:
        """Inspect or mutate LLM response before passing to agents/tools."""
        return response

    def before_tool(
        self,
        tool_name: str,
        tool_args: Dict[str, Any],
        metadata: Dict[str, Any],
    ) -> Tuple[bool, str, Dict[str, Any]]:
        """Intercept tool call before execution.
        Returns:
            (allow: bool, rejection_reason: str, modified_args: Dict[str, Any])
        """
        return True, "", tool_args

    def after_tool(
        self,
        tool_name: str,
        result: Any,
        metadata: Dict[str, Any],
    ) -> Any:
        """Inspect, sanitize or transform tool execution results."""
        return result

    def on_a2a_message(self, message: Dict[str, Any], metadata: Dict[str, Any]) -> Dict[str, Any]:
        """Intercept inter-agent (A2A) communications."""
        return message
