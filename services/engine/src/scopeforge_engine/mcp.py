"""MCP client (Phase 8). Explicit per-server trust, untrusted metadata.

MCP servers are DISABLED by default per run. Users review server identity,
exposed tools, data destinations, and permissions before enabling. Tool/prompt
descriptions are UNTRUSTED data: wrapped, never authority, cannot register
tools or mutate policy.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

try:
    from .store import atomic_write_text, read_json


except ImportError:  # direct script execution; script dir is on sys.path
    from store import atomic_write_text, read_json  # type: ignore[no-redef]


@dataclass(frozen=True)
class MCPServer:
    name: str
    command: str  # executable + args (typed argv, never shell)
    enabled: bool = False
    allowed_tools: tuple[str, ...] = ()
    # Provenance
    version: str = ""
    hash: str = ""


@dataclass
class MCPTool:
    name: str
    description: str
    input_schema: dict[str, Any]
    server: str
    # Marked untrusted at ingestion
    untrusted: bool = True


def _sanitize_description(text: str) -> str:
    """Wrap untrusted tool descriptions; strip policy/approval impersonation."""
    # Remove attempts to inject approval or policy language
    cleaned = re.sub(r"(?i)(approval|policy|scope|permission)\s*[:=]", "[filtered]", text)
    return f"[untrusted tool description from MCP] {cleaned[:2000]}"


class MCPRegistry:
    """Per-run MCP server registry. Persisted under .scopeforge/mcp.json."""

    def __init__(self, home: str | Path) -> None:
        self.home = Path(home)
        self.path = self.home / "mcp.json"

    def _load(self) -> dict[str, Any]:
        data = read_json(self.path, default={})
        return data if isinstance(data, dict) else {}

    def _save(self, data: dict) -> None:
        atomic_write_text(self.path, json.dumps(data, indent=2) + "\n")

    def list_servers(self) -> list[dict[str, Any]]:
        data = self._load()
        return [
            {"name": name, "command": cfg.get("command", ""), "enabled": bool(cfg.get("enabled")),
             "allowed_tools": list(cfg.get("allowed_tools", [])),
             "version": cfg.get("version", ""), "hash": cfg.get("hash", "")}
            for name, cfg in data.get("servers", {}).items()
        ]

    def add_server(self, name: str, command: str, *, version: str = "", hash: str = "") -> dict:
        if not re.fullmatch(r"[A-Za-z0-9_-]+", name):
            raise ValueError(f"server name must match [A-Za-z0-9_-]+, got {name!r}")
        if not command.strip():
            raise ValueError("server command is required")
        # No shell interpolation: command is stored as typed argv string
        data = self._load()
        servers = data.setdefault("servers", {})
        if name in servers:
            raise ValueError(f"server {name!r} already exists (remove first)")
        servers[name] = {"command": command, "enabled": False,
                         "allowed_tools": [], "version": version, "hash": hash}
        self._save(data)
        return {"name": name, "enabled": False, "command": command}

    def enable_server(self, name: str, *, allowed_tools: list[str] | None = None) -> dict:
        data = self._load()
        cfg = data.get("servers", {}).get(name)
        if cfg is None:
            raise ValueError(f"unknown server {name!r}")
        cfg["enabled"] = True
        if allowed_tools is not None:
            cfg["allowed_tools"] = list(allowed_tools)
        self._save(data)
        return {"name": name, "enabled": True, "allowed_tools": cfg["allowed_tools"]}

    def disable_server(self, name: str) -> dict:
        data = self._load()
        cfg = data.get("servers", {}).get(name)
        if cfg is None:
            raise ValueError(f"unknown server {name!r}")
        cfg["enabled"] = False
        self._save(data)
        return {"name": name, "enabled": False}

    def remove_server(self, name: str) -> bool:
        data = self._load()
        if name not in data.get("servers", {}):
            return False
        del data["servers"][name]
        self._save(data)
        return True

    def get_enabled(self) -> list[MCPServer]:
        data = self._load()
        out: list[MCPServer] = []
        for name, cfg in data.get("servers", {}).items():
            if cfg.get("enabled"):
                out.append(MCPServer(
                    name=name, command=cfg.get("command", ""),
                    enabled=True,
                    allowed_tools=tuple(cfg.get("allowed_tools", ())),
                    version=cfg.get("version", ""), hash=cfg.get("hash", "")))
        return out


class MCPClient:
    """JSON-RPC client for MCP tool discovery. Metadata is untrusted."""

    def __init__(self, registry: MCPRegistry) -> None:
        self.registry = registry

    async def list_tools(self, server: MCPServer) -> list[MCPTool]:
        """Discover tools from a single enabled server via JSON-RPC."""
        import httpx

        # For stdio transport, we spawn; for HTTP, we POST to command as URL
        # Simplified: treat command as HTTP endpoint if it starts with http
        if server.command.startswith("http"):
            return await self._list_tools_http(server)
        # Stdio path: would spawn subprocess — stub for now, returns empty with warning
        return []

    async def _list_tools_http(self, server: MCPServer) -> list[MCPTool]:
        import httpx

        payload = {"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}}
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.post(server.command, json=payload)
                resp.raise_for_status()
                data = resp.json()
        except Exception:
            return []
        tools = []
        for t in data.get("result", {}).get("tools", []):
            name = t.get("name", "")
            if server.allowed_tools and name not in server.allowed_tools:
                continue
            tools.append(MCPTool(
                name=name,
                description=_sanitize_description(t.get("description", "")),
                input_schema=t.get("inputSchema", t.get("input_schema", {})),
                server=server.name,
                untrusted=True,
            ))
        return tools

    async def call_tool(self, server: MCPServer, tool_name: str, arguments: dict) -> dict:
        """Call a tool on an enabled server. Only allowed tools."""
        if server.allowed_tools and tool_name not in server.allowed_tools:
            raise PermissionError(f"tool {tool_name!r} not in allowlist for {server.name!r}")
        if not server.enabled:
            raise PermissionError(f"server {server.name!r} is not enabled")
        import httpx

        payload = {"jsonrpc": "2.0", "id": 2, "method": "tools/call",
                   "params": {"name": tool_name, "arguments": arguments}}
        if server.command.startswith("http"):
            async with httpx.AsyncClient(timeout=30) as client:
                resp = await client.post(server.command, json=payload)
                resp.raise_for_status()
                return resp.json()
        raise NotImplementedError("stdio MCP transport not yet implemented (use HTTP endpoint)")


def sanitize_mcp_result(result: dict) -> dict:
    """Wrap MCP tool output as untrusted data."""
    return {"untrusted": True, "source": "mcp", "data": result}
