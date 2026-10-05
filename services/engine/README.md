# ScopeForge

> **Commercial-Grade Cybersecurity Multi-Agent Harness & Claude Code Style TUI**  
> Autonomous SecOps Orchestrator, LangGraph Multi-Agent Team, LangChain Tool Harness, MCP Bridge & Interactive Terminal UI.

[![PyPI version](https://img.shields.io/pypi/v/scopeforge.svg)](https://pypi.org/project/scopeforge/)
[![Python Versions](https://img.shields.io/pypi/pyversions/scopeforge.svg)](https://pypi.org/project/scopeforge/)
[![License: Apache-2.0](https://img.shields.io/badge/License-Apache_2.0-blue.svg)](https://opensource.org/licenses/Apache-2.0)
[![Built with Textual](https://img.shields.io/badge/UI-Textual-darkviolet.svg)](https://textual.textualize.io/)
[![Orchestration LangGraph](https://img.shields.io/badge/Orchestrator-LangGraph-orange.svg)](https://langchain-ai.github.io/langgraph/)

---

## ⚡ What is ScopeForge?

**ScopeForge** is a standalone, commercial-grade autonomous cybersecurity agent harness and terminal interface designed to work like **Claude Code** and **Open Code**, but specifically engineered with **ScopeGate Rules of Engagement (RoE)** safety guarantees.

Powered by **LangChain**, **LangGraph**, **Textual**, and the **Model Context Protocol (MCP)**, ScopeForge coordinates specialized cybersecurity agents to perform automated reconnaissance, vulnerability scanning, static code analysis (SAST), proof-of-concept verification, and executive reporting—or operates as a general-purpose AI coding and system administration assistant.

---

## 🚀 Installation & Quickstart

### 1. Install via pip / pipx / uv

```bash
# Recommended: Install using pipx (isolated environment)
pipx install scopeforge

# Or standard pip
pip install scopeforge

# Or run instantly without installation via uv
uvx scopeforge
```

### 2. Launch the Interactive TUI

Simply type `scopeforge` or the shorthand `sf` in your terminal:

```bash
scopeforge
# Or:
sf
```

ScopeForge will launch directly into the dark-themed reactive terminal UI.

---

## ⌨️ Modern Terminal Shortcuts

ScopeForge uses standard modern terminal `Ctrl+[key]` bindings:

| Shortcut | Secondary | Action | Description |
|:---:|:---:|:---|:---|
| <kbd>Ctrl+M</kbd> | <kbd>F1</kbd> | **Model Switcher** | Open live model picker, filter presets, or configure custom endpoints |
| <kbd>Ctrl+H</kbd> | <kbd>F2</kbd> | **Help Cheatsheet** | Show all slash commands, shortcuts, and agent roles |
| <kbd>Ctrl+O</kbd> | <kbd>F3</kbd> | **Toggle SecOps Mode** | Cycle policy gates (`plan` ↔ `live` ↔ `artifacts`) |
| <kbd>Ctrl+B</kbd> | <kbd>F4</kbd> | **Toggle Sidebar** | Expand or collapse agent telemetry and finding ledger |
| <kbd>Ctrl+W</kbd> | <kbd>F5</kbd> | **SecOps Wiki** | Open persistent knowledge base and user preferences |
| <kbd>Ctrl+Y</kbd> | <kbd>F6</kbd> | **Copy Last Response** | Copy the latest AI response to system clipboard |
| <kbd>Ctrl+T</kbd> | <kbd>F7</kbd> | **Toggle Native Mouse** | Switch between TUI clicks and terminal text selection |
| <kbd>Ctrl+L</kbd> | — | **Clear Screen** | Clear conversation stream and reset buffer |
| <kbd>Ctrl+Q</kbd> | — | **Quit** | Safely exit ScopeForge |

---

## 🤖 Multi-Agent Team (LangGraph)

ScopeForge routes tasks dynamically to specialized autonomous agents:

- 🧠 **Supervisor**: General architecture, system management, code discussion, and high-level strategy.
- 💻 **DevAgent**: Local code workspace operations, terminal sandbox commands, skill installation, and live web research.
- 🛰️ **ReconAgent**: Passive & active reconnaissance, port discovery telemetry, and attack surface enumeration.
- 🔬 **AuditAgent**: Static code auditing (SAST), CVE advisory correlation, and OWASP vulnerability identification.
- ⚡ **ExploitAgent**: Controlled proof-of-concept (PoC) validation within strict authorized bounds.
- 📋 **ReportAgent**: Executive SecOps summary, CVSS scoring, and remediation action plans.

Agents communicate asynchronously using the cryptographically verified **Agent-to-Agent (A2A) Protocol Bus**.

---

## 🛠️ LangChain Autonomous Tool-Calling Harness

ScopeForge provides real autonomous tool execution through LangChain:
- **Autonomous Tool Binding**: Models (`OpenRouter`, `Groq`, `DeepSeek`, `Claude`, `OpenAI`) are automatically bound to the complete toolsuite and active MCP tools via `chat_model.bind_tools()`.
- **Iterative Tool Execution**: The harness detects `tool_calls`, executes tools safely via the ScopeGate middleware pipeline, feeds `ToolMessage` outputs back into context, and synthesizes answers iteratively.
- **Built-in Tools**:
  - `bash_cli`: Sandboxed shell execution with dangerous-pattern blocking.
  - `google_web_search`: Live web search for fast technical lookups.
  - `view_file` / `write_file` / `edit_file`: Surgical code workspace inspection and editing.
  - `glob_files` / `grep_search`: Fast repository pattern discovery.
  - `git_status_tool` / `git_diff_tool` / `git_commit_tool`: Git version control operations.
  - `recon_port_scan` / `web_surface_probe`: Perimeter discovery tools.
  - `sast_code_audit` / `cve_advisory_search`: Vulnerability triage tools.

---

## 🌐 Model Support & Custom API Configuration

ScopeForge supports any OpenAI-compatible, Anthropic, or Ollama provider:

### Quick Presets (Zero Setup):
- `openrouter-free`: Meta-router for high-availability free frontier models (`openrouter/free`).
- `openrouter-free-nemotron`: NVIDIA Nemotron 550B Free.
- `groq-llama3`: Llama 3.3 70B on Groq Ultra-Fast inference.
- `ollama-llama3`: Offline local models via Ollama.

### Custom Endpoints & Third-Party Proxies:
Open the Model Switcher (<kbd>Ctrl+M</kbd>) or use slash commands:

```text
/model
/config set key <API_KEY>
/config set model <MODEL_ID>
/config set base <CUSTOM_URL>
```

ScopeForge features native paste de-duplication and custom endpoint normalization (no forced path appending).

---

## ⇄ Agent-to-Agent (A2A) Protocol Bus & Subagents

ScopeForge implements first-class Agent-to-Agent collaboration inspired by modern autonomous multi-agent architectures:

- **Dynamic Subagent Invocation**: Use `invoke_subagent(agent_name, task)` to delegate tasks to `recon`, `audit`, `exploit`, `report`, or `dev`.
- **A2A Message Broadcasting**: Use `send_a2a_message(recipient, intent, message)` to broadcast evidence, handovers, and results across the bus.
- **Interactive Telemetry**:
  ```text
  /a2a                                    # View registered agents, bus status, and recent message table
  /a2a send <agent> <intent> <message>    # Manually dispatch an A2A message
  /agent <name>                           # Direct next task to a specific specialist agent
  ```

---

## 🔌 Model Context Protocol (MCP) & Extensible Skills

### Model Context Protocol (MCP):
ScopeForge connects directly to external MCP servers:
```text
/mcp                                    # List all configured MCP servers & status
/mcp tools                              # View active tools registered across all MCP servers
/mcp add <name> <command>               # Add and immediately enable an MCP server
/mcp remove <name>                      # Remove an MCP server
/mcp enable <name>                      # Enable an existing server
/mcp disable <name>                     # Disable an existing server
```
Models can also invoke `add_mcp_server` and `list_mcp_servers` autonomously as LangChain tools.

### Extensible Skills (`SKILL.md`):
Install custom domain playbooks and guidelines directly from GitHub:
```text
/skill install https://github.com/Jakeschincariol/linkedin-agent-skill.git
/skill add my-playbook [description]    # Create a new custom skill template
/skill list                             # Discover all available local skills
/skill <name>                           # Toggle skill activation
```
Skills are automatically cloned, loaded into `.scopeforge/skills/` and `~/.scopeforge/skills/`, and dynamically injected into the model prompt when relevant keywords are triggered.

---

## ⚡ Long-Running Multi-Step Task Execution

Unlike basic wrappers that terminate prematurely after 2-4 tool calls, ScopeForge is built for real developer and security tasks:
- **Up to 25 Autonomous Iterations**: Run complete multi-step workflows (e.g. cloning a repository, installing skills, writing code, running tests, fixing errors, and auditing) without premature termination.
- **Graceful Synthesis Safeguard**: If an iteration budget is reached, ScopeForge automatically synthesizes all tool observations and outputs into a coherent, comprehensive final answer rather than cutting off in the middle.
- **Clean Token Streaming**: Token streaming seamlessly transitions between text generation and tool execution callouts with zero dropped chunks or duplicate messages.

---

## 🛡️ ScopeGate Rules of Engagement (RoE)

ScopeForge operates under 3 strict enforcement modes:
1. **PLAN (`plan`)**: Zero network egress. Generates falsifiable hypotheses, models attack surface, and plans audits.
2. **ARTIFACTS (`artifacts`)**: Inspects locally supplied source code, HAR files, and logs without external traffic.
3. **LIVE (`live`)**: Network tools enabled strictly against authorized scope domains and IPs.

---

## 📜 License

Licensed under the **Apache License, Version 2.0**.
