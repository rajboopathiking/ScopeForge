"""ScopeForge Middleware package for security guardrails, audit logging, and approvals."""
from .base import BaseMiddleware, ToolApprovalRequest
from .scope_gate import ScopeGateMiddleware
from .audit import AuditLoggingMiddleware
from .redaction import RedactionMiddleware
from .approval import ApprovalGateMiddleware
from .pipeline import MiddlewarePipeline, create_default_pipeline

__all__ = [
    "BaseMiddleware",
    "ToolApprovalRequest",
    "ScopeGateMiddleware",
    "AuditLoggingMiddleware",
    "RedactionMiddleware",
    "ApprovalGateMiddleware",
    "MiddlewarePipeline",
    "create_default_pipeline",
]
