# Model Context Protocol (MCP) Customization Guide

ScopeForge includes a first-class Model Context Protocol (MCP) client bridge. It allows connecting external MCP servers over stdio, discovering their tools, and exposing them directly to the multi-agent network with strict **ScopeGate security policies**.

---

## 1. How MCP Works in ScopeForge

1. **Explicit Server Registry**: Servers are declared in `.scopeforge/mcp.json`.
2. **Untrusted Metadata Protection**: In accordance with ScopeForge ADR-07 and Phase 8 specifications, tool descriptions provided by third-party MCP servers are treated as **untrusted data**. Injection attempts trying to impersonate human approvals or mutate policy are automatically filtered out.
3. **ScopeGate Integration**: When an agent invokes an MCP tool that contacts external networks or hosts, ScopeGate inspects the destination arguments against the authorized target list and execution mode (`PLAN`, `ARTIFACTS`, `LIVE`).

---

## 2. Configuration (`.scopeforge/mcp.json`)

MCP servers can be configured in `.scopeforge/mcp.json`:

```json
{
  "servers": {
    "github-security-mcp": {
      "command": "npx -y @modelcontextprotocol/server-github",
      "enabled": true,
      "allowed_tools": ["search_repositories", "get_file_contents"],
      "version": "0.1.0"
    },
    "filesystem-mcp": {
      "command": "npx -y @modelcontextprotocol/server-filesystem /tmp",
      "enabled": false,
      "allowed_tools": []
    },
    "fetch-mcp": {
      "command": "uvx mcp-server-fetch",
      "enabled": true,
      "allowed_tools": []
    },
    "postgres-audit-mcp": {
      "command": "npx -y @modelcontextprotocol/server-postgres postgresql://localhost/audit_db",
      "enabled": false
    }
  }
}
```

---

## 3. Managing MCP in the TUI

### List Registered Servers
```text
/mcp
# or:
/mcp list
```
Displays all configured servers, their enabled/disabled status, and execution command strings.

### Add a New MCP Server Dynamically
```text
/mcp add fetch uvx mcp-server-fetch
/mcp add git-server npx -y @modelcontextprotocol/server-github
```
Adds the server to `.scopeforge/mcp.json` and immediately enables it.

### Enable or Disable Servers
```text
/mcp enable filesystem-mcp
/mcp disable github-security-mcp
```

### Inspect in Sidebar
Click the **MCP Protocol** tab in the sidebar (<kbd>F2</kbd>) to observe active servers and their tool availability.

---

## 4. Popular Security MCP Servers

| Server | Stdio Command | Purpose |
|---|---|---|
| **Fetch** | `uvx mcp-server-fetch` | Safe web page content fetching & conversion to markdown |
| **GitHub** | `npx -y @modelcontextprotocol/server-github` | Code repository inspection, commit diffs, PR auditing |
| **Filesystem** | `npx -y @modelcontextprotocol/server-filesystem /path` | Sandboxed local file analysis (HAR, logs, code) |
| **PostgreSQL** | `npx -y @modelcontextprotocol/server-postgres <conn_str>` | Database schema review and SQL access auditing |
| **Brave Search**| `npx -y @modelcontextprotocol/server-brave-search` | Web OSINT and CVE advisory intelligence |
