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
        auto_adapt: bool = True,
    ):
        super().__init__(name="ScopeGate", priority=10)
        self.authorized_scopes: Set[str] = set(authorized_scopes or [
            "authorized.example",
            "*.example.com",
            "localhost",
            "127.0.0.1",
            "thangarasusamayal.vercel.app",
            "pentest-ground.com",
        ])
        self.forbidden_scopes: Set[str] = set(forbidden_scopes or ["*.gov", "*.mil", "production.bank.com"])
        self.mode: str = mode.lower()  # "plan", "artifacts", "live"
        self.auto_adapt: bool = auto_adapt
        self.dynamic_history: List[Dict[str, str]] = []

    def set_mode(self, mode: str):
        self.mode = mode.lower()

    def add_scope(self, target: str):
        cleaned = self._clean_host(target)
        if cleaned:
            self.authorized_scopes.add(cleaned)
            if "." in cleaned and not self._is_ip(cleaned):
                self.authorized_scopes.add(f"*.{cleaned}")

    def remove_scope(self, target: str):
        cleaned = self._clean_host(target)
        if cleaned:
            self.authorized_scopes.discard(cleaned)
            self.authorized_scopes.discard(f"*.{cleaned}")

    @staticmethod
    def _clean_host(raw: str) -> str:
        """Normalize URL or host to a lowercase hostname without port."""
        if not raw:
            return ""
        val = raw.strip().lower()
        if "://" in val:
            parsed = urlparse(val)
            val = parsed.hostname or val
        if ":" in val and not val.startswith("["):
            val = val.split(":")[0]
        return val.strip("/")

    @staticmethod
    def _is_ip(host: str) -> bool:
        """Check if string is an IPv4 or loopback identifier."""
        if host in ("localhost", "127.0.0.1", "0.0.0.0", "::1"):
            return True
        parts = host.split(".")
        return len(parts) == 4 and all(p.isdigit() and 0 <= int(p) <= 255 for p in parts)

    @staticmethod
    def _get_base_domain(host: str) -> str:
        """Extract registrable parent domain (e.g. 'pentest-ground.com' from 'api.pentest-ground.com')."""
        parts = host.split(".")
        if len(parts) >= 2:
            return ".".join(parts[-2:])
        return host

    def _is_forbidden(self, host: str) -> Tuple[bool, str]:
        """Check target host against forbidden scope list."""
        for forbidden in self.forbidden_scopes:
            if forbidden.startswith("*."):
                suffix = forbidden[2:]
                if host == suffix or host.endswith("." + suffix):
                    return True, f"Target '{host}' matches forbidden scope pattern '{forbidden}'"
            elif host == forbidden:
                return True, f"Target '{host}' is explicitly in the forbidden targets list"
        return False, ""

    def adapt_target(self, target: str, reason: str = "Dynamic adaptation") -> Tuple[bool, str]:
        """Dynamically expand authorized scopes to include a target if not forbidden."""
        clean = self._clean_host(target)
        if not clean:
            return False, "Empty target host"

        forbidden, forb_reason = self._is_forbidden(clean)
        if forbidden:
            return False, f"Cannot adapt scope: {forb_reason}"

        added = []
        if clean not in self.authorized_scopes:
            self.authorized_scopes.add(clean)
            added.append(clean)

        if "." in clean and not self._is_ip(clean):
            wc = f"*.{clean}"
            if wc not in self.authorized_scopes:
                self.authorized_scopes.add(wc)
                added.append(wc)
            base = self._get_base_domain(clean)
            if base != clean:
                base_wc = f"*.{base}"
                if base_wc not in self.authorized_scopes:
                    self.authorized_scopes.add(base_wc)
                    added.append(base_wc)

        self.dynamic_history.append({"target": clean, "reason": reason, "added": str(added)})
        return True, f"Scope dynamically adapted for '{clean}' ({', '.join(added) if added else 'already authorized'})"

    def adapt_to_mission(self, mission_text: str, explicit_scope: Optional[List[str]] = None) -> List[str]:
        """Extract targets from mission description and explicit scopes, dynamically adapting ScopeGate."""
        adapted: List[str] = []

        # 1. Adapt any explicitly provided scopes
        if explicit_scope:
            for s in explicit_scope:
                ok, _ = self.adapt_target(s, reason="Explicit mission scope")
                if ok:
                    adapted.append(self._clean_host(s))

        # 2. Extract candidate targets, domains, and URLs from mission text
        if mission_text:
            url_matches = re.findall(r"https?://([a-zA-Z0-9][-a-zA-Z0-9.]*[a-zA-Z0-9]|\d{1,3}(?:\.\d{1,3}){3})(?::\d+)?", mission_text)
            for host in url_matches:
                ok, _ = self.adapt_target(host, reason="Extracted from mission URL")
                if ok:
                    adapted.append(self._clean_host(host))

            # Remove URLs first to avoid partial matching on schemes or paths
            text_without_urls = re.sub(r"https?://[^\s]+", "", mission_text)
            domain_matches = re.findall(r"\b([a-zA-Z0-9](?:[a-zA-Z0-9\-]*\.)+(?:com|org|net|io|app|dev|in|co|ai|edu|local))\b", text_without_urls, re.IGNORECASE)
            for d in domain_matches:
                ok, _ = self.adapt_target(d, reason="Extracted domain from mission text")
                if ok:
                    adapted.append(self._clean_host(d))

        return list(dict.fromkeys(adapted))

    def _extract_target(self, tool_args: Dict[str, Any]) -> str:
        """Extract domain or IP from common tool arguments or command strings."""
        for key in ("target", "host", "url", "domain", "endpoint", "ip"):
            if key in tool_args and isinstance(tool_args[key], str):
                return self._clean_host(tool_args[key])

        # Inspect CLI command string for URLs or target hosts (e.g. bash_cli, bash_security_exec)
        if "command" in tool_args and isinstance(tool_args["command"], str):
            cmd = tool_args["command"]
            url_m = re.search(r"https?://([a-zA-Z0-9][-a-zA-Z0-9.]*[a-zA-Z0-9]|\d{1,3}(?:\.\d{1,3}){3})(?::\d+)?", cmd)
            if url_m:
                return self._clean_host(url_m.group(1))
            cli_m = re.search(r"\b(?:curl|nmap|ping|traceroute|dig|nslookup|ssh|nc|telnet)\s+(?:-[^\s]+\s+)*([a-zA-Z0-9][-a-zA-Z0-9.]*\.[a-zA-Z]{2,})\b", cmd)
            if cli_m:
                return self._clean_host(cli_m.group(1))

        return ""

    def _is_authorized(self, host: str) -> Tuple[bool, str]:
        if not host:
            return True, "No external host target identified"

        clean = self._clean_host(host)

        # 1. Check forbidden scopes first
        forbidden, reason = self._is_forbidden(clean)
        if forbidden:
            return False, reason

        # 2. Check exact and wildcard matches
        for auth in list(self.authorized_scopes):
            if auth.startswith("*."):
                suffix = auth[2:]
                if clean == suffix or clean.endswith("." + suffix):
                    return True, "Matched wildcard authorized scope"
            elif clean == auth:
                return True, "Matched exact authorized scope"

        # 3. Dynamic Subdomain Hierarchy Adaptation
        # If clean is a subdomain of an authorized domain (e.g. 'api.example.com' of 'example.com')
        base = self._get_base_domain(clean)
        for auth in list(self.authorized_scopes):
            auth_clean = auth[2:] if auth.startswith("*.") else auth
            if auth_clean and (clean.endswith("." + auth_clean) or (base == auth_clean and base != clean)):
                if self.auto_adapt:
                    self.adapt_target(clean, reason=f"Subdomain of authorized '{auth_clean}'")
                    return True, f"Dynamically adapted: '{clean}' belongs to authorized domain family '{auth_clean}'"
                return True, "Matched authorized domain family"

        return False, f"Target '{clean}' is NOT in authorized scope list: {sorted(self.authorized_scopes)}"

    def before_tool(
        self,
        tool_name: str,
        tool_args: Dict[str, Any],
        metadata: Dict[str, Any],
    ) -> Tuple[bool, str, Dict[str, Any]]:
        # 1. Mode Enforcement
        # capture_screenshot performs live network egress (page navigation),
        # so it is gated exactly like the other live tools (no silent bypass).
        network_tools = {"recon_port_scan", "web_surface_probe", "falsifiable_poc_runner", "bash_security_exec", "capture_screenshot"}

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
