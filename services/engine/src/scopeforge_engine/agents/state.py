"""State definition for LangGraph cybersecurity multi-agent orchestration."""
from __future__ import annotations

from typing import Annotated, Any, Dict, List, Optional
from typing_extensions import TypedDict
from langchain_core.messages import BaseMessage
from langgraph.graph.message import add_messages


class AgentState(TypedDict):
    """The unified state shared across all agents in the LangGraph network."""
    messages: Annotated[List[BaseMessage], add_messages]
    active_agent: str
    mission: str
    mode: str
    scope: List[str]
    findings: List[Dict[str, Any]]
    evidence_store: List[str]
    a2a_log: List[Dict[str, Any]]
    user_preferences: str
    rag_context: str
    pending_approval: Optional[Dict[str, Any]]
    next_step: Optional[str]
