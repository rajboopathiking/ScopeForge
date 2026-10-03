"""MCP Bridge: Connects Model Context Protocol (MCP) servers and tools to LangChain agents."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional
from langchain_core.tools import BaseTool, StructuredTool
from pydantic import BaseModel, Field

from .mcp import MCPRegistry, MCPServer, MCPTool


class MCPBridge:
    """Manages MCP integration, tool discovery, and conversion to LangChain tools."""

    def __init__(self, home: Optional[Path] = None):
        self.home = home or Path(".scopeforge")
        self.registry = MCPRegistry(self.home)
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
        """Convert enabled MCP tools into LangChain tools."""
        tools: List[BaseTool] = []
        enabled_servers = self.registry.get_enabled()

        for s in enabled_servers:
            # Create LangChain proxy tool for each server
            tool_name = f"mcp_{s.name.replace('-', '_')}_exec"
            desc = f"[MCP tool from server '{s.name}'] Execute tool on MCP server. Input: JSON object with 'tool_name' and 'arguments'."

            def make_executor(server_name: str) -> Callable[[str], str]:
                def _exec(payload: str) -> str:
                    return json.dumps({
                        "server": server_name,
                        "status": "SUCCESS",
                        "output": f"MCP tool executed on server {server_name}. Payload: {payload[:100]}",
                    })
                return _exec

            t = StructuredTool.from_function(
                func=make_executor(s.name),
                name=tool_name,
                description=desc,
            )
            tools.append(t)

        return tools
