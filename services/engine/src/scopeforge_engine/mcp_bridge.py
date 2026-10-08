"""MCP Bridge: Connects Model Context Protocol (MCP) servers and tools to LangChain agents."""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from langchain_core.tools import BaseTool, StructuredTool
from pydantic import BaseModel, Field

from .mcp import MCPClient, MCPRegistry, MCPServer, MCPTool


class MCPBridge:
    """Manages MCP integration, tool discovery, and conversion to LangChain tools."""

    def __init__(self, home: Optional[Path] = None):
        self.home = home or Path(".scopeforge")
        self.registry = MCPRegistry(self.home)
        self.client = MCPClient(self.registry)
        self._tools_cache: Dict[str, Tuple[float, List[MCPTool]]] = {}
        self._cache_ttl_s = 60.0
        self._ensure_sample_servers()

    def _ensure_sample_servers(self):
        """Add sample security MCP servers if registry is empty."""
        try:
            servers = self.registry.list_servers()
            if not servers:
                self.registry.add_server(
                    name="github-security-mcp",
                    command="npx -y @modelcontextprotocol/server-github",
                    version="0.1.0",
                )
                self.registry.add_server(
                    name="filesystem-mcp",
                    command="npx -y @modelcontextprotocol/server-filesystem /tmp",
                    version="0.1.0",
                )
        except Exception:
            pass

    def list_servers(self) -> List[Dict[str, Any]]:
        return self.registry.list_servers()

    def enable_server(self, name: str) -> Dict[str, Any]:
        return self.registry.enable_server(name)

    def disable_server(self, name: str) -> Dict[str, Any]:
        return self.registry.disable_server(name)

    def get_langchain_tools(self) -> List[BaseTool]:
        """Expose one real LangChain tool per discovered MCP tool.

        Commercial-agent behavior (opencode/claude code): discover via
        `tools/list` on each ENABLED server, cache briefly, and bind real
        `tools/call` executors. Never fabricate success — if discovery fails
        (server missing, not installed, timeout), that server contributes zero
        tools and the agent sees an honest error only when it tries to call.
        """
        tools: List[BaseTool] = []
        for s in self.registry.get_enabled():
            for t in self._discover_cached(s):
                tools.append(self._make_tool(s, t))
        return tools

    def _discover_cached(self, server: MCPServer) -> List[MCPTool]:
        now = time.monotonic()
        hit = self._tools_cache.get(server.name)
        if hit and (now - hit[0]) < self._cache_ttl_s:
            return hit[1]
        try:
            found = self.client.list_tools_sync(server, timeout_s=15.0)
        except Exception:
            found = []
        self._tools_cache[server.name] = (now, found)
        return found

    def refresh(self, server_name: Optional[str] = None) -> Dict[str, Any]:
        """Clear discovery cache (e.g. after `/mcp enable`) so future MCPs appear."""
        if server_name:
            self._tools_cache.pop(server_name, None)
            matched = [s for s in self.registry.get_enabled() if s.name == server_name]
            count = len(self._discover_cached(matched[0])) if matched else 0
            return {"server": server_name, "tools": count}
        self._tools_cache.clear()
        total = sum(len(self._discover_cached(s)) for s in self.registry.get_enabled())
        return {"tools": total}

    @staticmethod
    def _tool_name(server: MCPServer, tool: MCPTool) -> str:
        base = f"mcp_{server.name.replace('-', '_')}_{tool.name.replace('-', '_')}"
        return base[:64]

    def _make_tool(self, server: MCPServer, tool: MCPTool) -> BaseTool:
        desc = (
            f"[MCP:{server.name}] {tool.description}\n"
            f"Input: JSON object matching the tool schema. Server command: `{server.command}`."
        )

        def _exec(payload: Any) -> str:
            try:
                args = json.loads(payload) if isinstance(payload, str) else (payload or {})
                if not isinstance(args, dict):
                    args = {"input": args}
            except Exception:
                args = {"input": str(payload)}
            try:
                res = self.client.call_tool_sync(server, tool.name, args, timeout_s=60.0)
                return json.dumps(res)[:8000]
            except Exception as e:
                return json.dumps({"success": False, "server": server.name, "tool": tool.name, "error": str(e)[:500]})

        return StructuredTool.from_function(
            func=_exec,
            name=self._tool_name(server, tool),
            description=desc,
        )
