"""ScopeGate Middleware: Enforces target boundaries, authorized scopes, and execution modes."""
from __future__ import annotations

import re
from typing import Any, Dict, List, Set, Tuple
from urllib.parse import urlparse

from .base import BaseMiddleware


class ScopeGateMiddleware(BaseMiddleware):
    """Enforces cybersecurity Rules of Engagement (RoE).
    Prevents unauthorized target probing and enforces execution modes:
    - PLAN: Absolutely NO target contact, network scans, or live execution.
    - ARTIFACTS: Only local fixtures/logs/HAR/code analysis.
    - LIVE: Strictly bounded to declared authorized targets.
    """

    def __init__(
        self,
        authorized_scopes: List[str] | None = None,
        forbidden_scopes: List[str] | None = None,
        mode: str = "plan",
    ):
        super().__init__(name="ScopeGate", priority=10)
        self.authorized_scopes: Set[str] = set(authorized_scopes or ["authorized.example", "*.example.com", "localhost", "127.0.0.1", "thangarasusamayal.vercel.app", "pentest-ground.com"])
        self.forbidden_scopes: Set[str] = set(forbidden_scopes or ["*.gov", "*.mil", "production.bank.com"])
        self.mode: str = mode.lower()  # "plan", "artifacts", "live"

    def set_mode(self, mode: str):
        self.mode = mode.lower()

    def add_scope(self, target: str):
        self.authorized_scopes.add(target.strip())

    def remove_scope(self, target: str):
        self.authorized_scopes.discard(target.strip())

    def _extract_target(self, tool_args: Dict[str, Any]) -> str:
        """Extract domain or IP from common tool arguments."""
        for key in ("target", "host", "url", "domain", "endpoint", "ip"):
            if key in tool_args and isinstance(tool_args[key], str):
                val = tool_args[key]
                if "://" in val:
                    parsed = urlparse(val)
                    return parsed.hostname or val
                return val.split(":")[0]
        return ""

    def _is_authorized(self, host: str) -> Tuple[bool, str]:
        if not host:
            return True, "No external host target identified"

        # Check forbidden scopes first
        for forbidden in self.forbidden_scopes:
            if forbidden.startswith("*."):
                suffix = forbidden[2:]
                if host == suffix or host.endswith("." + suffix):
                    return False, f"Target '{host}' matches forbidden scope pattern '{forbidden}'"
            elif host == forbidden:
                return False, f"Target '{host}' is explicitly in the forbidden targets list"

        # Check authorized scopes
        for auth in self.authorized_scopes:
            if auth.startswith("*."):
                suffix = auth[2:]
                if host == suffix or host.endswith("." + suffix):
                    return True, "Matched wildcard authorized scope"
            elif host == auth:
                return True, "Matched exact authorized scope"

        return False, f"Target '{host}' is NOT in authorized scope list: {sorted(self.authorized_scopes)}"

    def before_tool(
        self,
        tool_name: str,
        tool_args: Dict[str, Any],
        metadata: Dict[str, Any],
    ) -> Tuple[bool, str, Dict[str, Any]]:
        # 1. Mode Enforcement
        network_tools = {"recon_port_scan", "web_surface_probe", "falsifiable_poc_runner", "bash_security_exec"}

        if self.mode == "plan":
            if tool_name in network_tools:
                return (
                    False,
                    f"ScopeGate Violation: Mode is currently 'PLAN'. Live tool '{tool_name}' cannot contact targets. Switch mode to 'live' to proceed.",
                    tool_args,
                )

        if self.mode == "artifacts":
            if tool_name in network_tools:
                return (
                    False,
                    f"ScopeGate Violation: Mode is 'ARTIFACTS'. External contact via '{tool_name}' is forbidden.",
                    tool_args,
                )

        # 2. Scope boundary check
        target = self._extract_target(tool_args)
        if target:
            authorized, reason = self._is_authorized(target)
            if not authorized:
                return (
                    False,
                    f"ScopeGate Policy Enforcer: Access to '{target}' blocked! {reason}",
                    tool_args,
                )

        return True, "", tool_args
