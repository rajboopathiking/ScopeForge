# ScopeForge: Complete Master Manual & Cybersecurity Agent Harness Guide

ScopeForge is an independent, model-agnostic cybersecurity agent harness inspired by the sleek ergonomics of **Claude Code** and **Open Code**. It is natively powered by **Python Textual**, **LangChain**, **LangGraph**, **LlamaIndex RAG**, and **A2A (Agent-to-Agent) Protocol**.

---

## 1. System Architecture

```mermaid
flowchart TD
    User([Security Researcher]) <--> TUI[Textual TUI - Claude / Open Code Style]
    
    subgraph UI_Layer [Terminal UI Layer]
        TUI --> Header[Header Status Badges\nModel | Mode | Scope | Agent | Cost]
        TUI --> ChatLog[Main Workspace\nChat Stream | Tool Cards | Finding Badges]
        TUI --> Sidebar[Sidebar Tabs\nSkills | Agents & A2A | Scope | Wiki | RAG | MCP | Findings]
        TUI --> InputBar[Interactive Input Bar\nSlash Commands / Auto-Complete]
    end

    TUI <--> MiddlewarePipeline[Middleware Pipeline]

    subgraph Security_Gate [Security Middleware & RoE Enforcer]
        MiddlewarePipeline --> Redaction[Redaction Interceptor\nAPI Keys, Secrets, JWTs]
        MiddlewarePipeline --> ScopeGate[ScopeGate RoE Policy\nPLAN | ARTIFACTS | LIVE]
        MiddlewarePipeline --> Approval[Approval Gate\nHuman-in-the-Loop Dialog]
        MiddlewarePipeline --> AuditLog[Audit Logger\nAppend-Only SHA256 Chain]
    end

    MiddlewarePipeline <--> MultiAgentOrchestrator[LangGraph Multi-Agent Engine]

    subgraph Agent_Core [LangGraph StateGraph & Multi-Agent Network]
        MultiAgentOrchestrator --> Supervisor[Supervisor Agent\nMission Decomposition & Routing]
        Supervisor <--> A2ABus[A2A Message Bus\nCryptographically Signed Messages]
        A2ABus <--> ReconAgent[ReconAgent\nPerimeter & Surface Audit]
        A2ABus <--> AuditAgent[AuditAgent\nSAST & CVE Correlation]
        A2ABus <--> ExploitAgent[ExploitAgent\nFalsifiable PoC Verification]
        A2ABus <--> ReportAgent[ReportAgent\nCVSS v3.1 & Synthesis]
        A2ABus <--> CustomAgents[Custom User Agents\n.scopeforge/agents/*.yaml]
    end

    subgraph Knowledge_Memory [Knowledge & Context Subsystems]
        Supervisor <--> LlamaRAG[LlamaIndex RAG Engine\nOWASP, CVEs, RoE Policies]
        Supervisor <--> SecWiki[SecWiki Memory\npreferences.md, targets, playbooks]
        Supervisor <--> SkillsMgr[Skills Manager\n.scopeforge/skills/*/SKILL.md]
    end

    subgraph Tooling_Ecosystem [Execution & Integration Layer]
        Agent_Core --> CyberTools[Native Security Tools\nPort Scan, Web Probe, SAST, CVE, PoC, Evidence]
        Agent_Core --> MCPBridge[MCP Client Bridge\nExternal Stdio MCP Servers]
    end

    subgraph LLM_Providers [Model Provider Abstraction]
        Agent_Core --> ProviderMgr[Provider Manager]
        ProviderMgr --> Anthropic[Anthropic Claude 3.7 / 3.5]
        ProviderMgr --> OpenAI[OpenAI GPT-4o / o1]
        ProviderMgr --> Ollama[Local Ollama Llama 3 / DeepSeek-R1]
        ProviderMgr --> OpenRouter[OpenRouter / Groq]
        ProviderMgr --> CustomEndpoints[Local vLLM / LMStudio]
        ProviderMgr --> MockSecOps[Offline Mock Evaluator]
    end
```

---

## 2. Launching the Terminal UI

Run either the root launcher or entrypoint:

```bash
# Launch directly via Python:
python scopeforge_tui.py

# Or via project entrypoint:
uv run scopeforge-tui
```

### Layout Overview
1. **Header Bar**:
   - `⚡ SCOPEFORGE`: Brand badge.
   - `[Model]`: Active model identifier (e.g. `claude-3-7-sonnet`, `gpt-4o`, `ollama-llama3`).
   - `[Mode]`: Safety mode badge (`PLAN` in green, `ARTIFACTS` in yellow, `LIVE` in red).
   - `[Scope]`: Active primary target (e.g. `authorized.example`).
   - `[Agent]`: Active agent specialist (e.g. `Supervisor`, `Recon`, `Audit`).
   - `[Tokens / Cost]`: Real-time session token counter and estimated spend.
2. **Left Panel (Chat Stream)**:
   - Formatted markdown response rendering with syntax highlighting.
   - Collapsible tool execution cards displaying arguments, ScopeGate authorization, and results.
   - Inter-agent A2A message banners (`⇄ A2A Protocol: Supervisor -> ReconAgent [TASK_DELEGATION]`).
   - Vulnerability cards with severity badges (`CRITICAL`, `HIGH`, `MEDIUM`).
3. **Right Panel (Sidebar — Toggle with <kbd>F2</kbd>)**:
   - Tab 1: **Skills**: Discovered playbooks from `.scopeforge/skills/` with trigger keywords.
   - Tab 2: **Agents & A2A**: Active multi-agent team roster and real-time message stream.
   - Tab 3: **Scope & Policy**: Authorized target allowlist, forbidden patterns, and mode rules.
   - Tab 4: **Wiki & Memory**: User preferences (`preferences.md`), targets, and methodologies.
   - Tab 5: **RAG Store**: Indexed document counters and quick knowledge search.
   - Tab 6: **MCP Protocol**: Status and tool inventory of connected MCP servers.
   - Tab 7: **Findings**: Ledger of confirmed vulnerabilities and CVSS scores.
4. **Bottom Prompt Bar**:
   - Monospace input `❯ ` with slash command autocomplete and command history (<kbd>↑</kbd> / <kbd>↓</kbd>).

---

## 3. Keyboard Shortcuts & Slash Commands

### Keyboard Shortcuts
| Key | Action |
|---|---|
| <kbd>F1</kbd> | Open interactive Help & Documentation modal |
| <kbd>F2</kbd> | Toggle Right Sidebar on / off |
| <kbd>F3</kbd> | Cycle Safety Mode (`PLAN` ➔ `ARTIFACTS` ➔ `LIVE`) |
| <kbd>F4</kbd> | Open LLM Wiki & User Preferences Viewer / Editor |
| <kbd>F5</kbd> | Clear terminal conversation history |
| <kbd>Tab</kbd> | Switch navigation focus between prompt and sidebar |
| <kbd>Esc</kbd> | Close any open modal screen |
| <kbd>Ctrl+C</kbd> | Graceful exit |

### Slash Commands Reference
| Command | Arguments | Description |
|---|---|---|
| **`/init`** | None | Initialize `SCOPEFORGE.md` project memory in repo root |
| **`/diff`** | None | View git diff of working tree changes in chat stream |
| **`/commit`** | `[message]` | Commit staged changes or auto-generate a commit message |
| **`/review`** | None | Autonomous Claude Code review of git changes for bugs & security flaws |
| **`/compact`** | None | Compact conversation history to free up LLM token budget |
| **`/doctor`** | None | Run full system health & diagnostic check (Python, git, LLM, MCP, audit log) |
| **`/pr`** | None | Generate formatted GitHub Pull Request description template |
| `/help` | None | Open interactive documentation cheat sheet |
| `/model` | `[name]` | Switch LLM or open interactive model selector modal |
| `/mode` | `<plan\|artifacts\|live>` | Change ScopeGate safety execution mode |
| `/agent` | `<name>` | Direct task specifically to `dev`, `recon`, `audit`, `exploit`, `report` |
| `/skill` | `[list\|<name>]` | Discover or toggle specialized agent skills |
| `/mcp` | `[list\|add\|enable\|disable]` | Manage Model Context Protocol (MCP) external servers |
| `/config` | `[model]` | Inspect active configuration or open model configuration center |
| `/status` | None | Display full mission status and agent team health |
| `/cost` | None | View session token usage and estimated costs |
| `/rag` | `<query>` | Query LlamaIndex cybersecurity store (OWASP, CVEs, RoE) |
| `/rag ingest`| `<path>` | Ingest a local advisory or document into RAG |
| `/wiki` | None | Open interactive persistent memory editor |
| `/scope` | `[add <target>]` | View or add target domains to ScopeGate allowlist |
| `/findings`| None | View all discovered vulnerabilities and CVSS scores |
| `/report` | None | Compile and export full SecOps report |
| `/clear` | None | Clear chat log |
| `/quit` | None | Exit ScopeForge |

---

## 4. LLM Provider Management & In-Terminal UI Configuration

### In-Terminal TUI Model & Provider Configuration Center
Open the interactive Configuration Center directly from the prompt:
```text
/model
# or:
/config model
```
- **Tab 1: 📋 Active Models & Quick Switch**: Browse registered providers in a live table and click `Activate Selected`.
- **Tab 2: ⚙️ Configure Provider in UI**:
  - One-click presets: `[OpenRouter Free]`, `[DeepSeek R1 Free]`, `[Llama 3.3 Free]`, `[Gemini 2.0 Free]`, `[Claude 3.5]`, `[Local Ollama]`.
  - Form inputs: Provider Platform, Name, Model ID, Masked API Key, Base URL, Temperature, Max Tokens.
  - Buttons: **`💾 Save & Activate in UI`** (instantly updates `.scopeforge/providers.yaml` and active session) and **`Save to Config Only`**.

### OpenRouter Free Tier Models (Zero Cost)
ScopeForge supports OpenRouter's free router and free-tier reasoning models out of the box:
```text
/model free                                      # Switch to OpenRouter Free tier (openrouter/free)
/model openrouter deepseek/deepseek-r1:free      # DeepSeek R1 reasoning free model
/model openrouter meta-llama/llama-3.3-70b:free  # Meta Llama 3.3 70B instruct free model
/model openrouter google/gemini-2.0-flash-exp:free # Gemini 2.0 Flash free model
```

### Switching Models via Fast Commands
```text
/model openrouter-free        # OpenRouter free tier (openrouter/free)
/model claude-3-7-sonnet      # Anthropic Claude 3.7 Sonnet
/model gpt-4o                 # OpenAI GPT-4o
/model ollama-llama3          # Local Ollama Llama 3
/model ollama-deepseek-r1     # Local Ollama DeepSeek R1
/model groq-llama3            # Groq ultra-low latency
/model mock-secops            # Offline zero-API-key simulation
```

### Setting API Keys
```bash
export OPENROUTER_API_KEY="sk-or-v1-..."
export ANTHROPIC_API_KEY="sk-ant-..."
export OPENAI_API_KEY="sk-..."
export GROQ_API_KEY="gsk_..."
```

### Adding Custom Endpoints (`.scopeforge/providers.yaml`)
To connect private or local vLLM / LMStudio / Ollama endpoints:
```yaml
active: openrouter-free
providers:
  local-vllm:
    name: local-vllm
    provider: custom
    model: Qwen/Qwen2.5-Coder-32B-Instruct
    api_base: http://localhost:8000/v1
    api_key: none
    temperature: 0.1
```

---

## 5. Skills Customization (`SKILL.md`)

Skills empower agents with specialized methodologies. They are stored in:
`.scopeforge/skills/<skill-name>/SKILL.md`

### Anatomy of a Skill
```markdown
---
name: jwt-security-audit
description: Testing JSON Web Token signature bypasses and algorithm confusion.
triggers: ["jwt", "token", "signature", "none algorithm", "jwks", "bearer"]
author: SecOps Team
version: 1.0.0
---

# JWT Security Testing Methodology

When evaluating endpoints using JWT authentication:
1. **Algorithm Confusion**: Test `alg: "none"` and RS256-to-HS256 public key confusion.
2. **Header Injections**: Inspect `jwk`, `jku`, and `kid` path traversal (`/dev/null`).
3. **ScopeGate Check**: Verify target domain is in authorized scope before sending tokens.
```

### Auto-Detection
Whenever you enter a query containing words in `triggers` (e.g. *"Check if this JWT token allows signature bypass"*), ScopeForge **automatically matches and injects** that skill's instructions into the agent's prompt!

---

## 6. Model Context Protocol (MCP) Customization

External tools can be connected via Model Context Protocol stdio servers.

### Configuration (`.scopeforge/mcp.json`)
```json
{
  "servers": {
    "github-security-mcp": {
      "command": "npx -y @modelcontextprotocol/server-github",
      "enabled": true,
      "allowed_tools": ["search_repositories", "get_file_contents"]
    },
    "fetch-mcp": {
      "command": "uvx mcp-server-fetch",
      "enabled": true
    }
  }
}
```

### Adding MCP Servers in the TUI
```text
/mcp add fetch uvx mcp-server-fetch
/mcp enable fetch
/mcp disable github-security-mcp
```
All MCP tools are automatically intercepted by **ScopeGate** to verify that target parameters comply with authorized boundary rules.

---

## 7. ScopeGate Rules of Engagement & Safety

ScopeForge prevents unauthorized or destructive scanning:

1. **`PLAN` Mode (Default)**:
   - Target contact is strictly prohibited (0 network packets sent).
   - Only architectural modeling, SAST code review, and hypothesis design run.
2. **`ARTIFACTS` Mode**:
   - Offline analysis of provided local fixtures (HAR files, PCAPs, logs, code).
3. **`LIVE` Mode**:
   - Active network probing permitted **only** against declared authorized targets (`authorized.example`, `localhost`).
   - Automatically blocks requests to `*.gov`, `*.mil`, or unapproved hosts.
4. **Approval Gate**:
   - Dangerous tools (`falsifiable_poc_runner`, `bash_security_exec`) trigger a modal approval dialog requesting explicit operator consent.
5. **Redaction Interceptor**:
   - Automatically masks API keys (`sk-...`), private keys, and passwords from logs and responses.
6. **Audit Ledger**:
   - Appends all events to `.scopeforge/audit.jsonl` with SHA256 cryptographic hash chaining (`prev_hash`).

---

## 8. Why ScopeForge is Superior for Cybersecurity

| Dimension | ScopeForge | Generic Coding Agents (Claude Code, OpenHands) | Generic Multi-Agent (CrewAI, AutoGen) | 1st-Gen Sec Wrappers (PentestGPT) |
|---|:---:|:---:|:---:|:---:|
| **Rules of Engagement (ScopeGate)** | **Built-in Policy Enforcer** (`PLAN`, `ARTIFACTS`, `LIVE`) | ❌ None (host-blind) | ❌ None | ⚠️ Manual / Prompt-only |
| **Out-of-Scope Blocking** | **Regex & CIDR Guardrail** | ❌ No boundary checking | ❌ No boundary checking | ❌ None |
| **Evidence Ledger** | **Cryptographic SHA256 Chained Store** | ❌ Ephemeral output | ❌ None | ⚠️ Raw text dumps |
| **Verification Standard** | **Falsifiable Differential** ($\Delta = S_1 - S_0$) | ❌ Speculative hallucinations | ❌ Speculative chat | ⚠️ Basic advice |
| **Multi-Agent Architecture** | **LangGraph StateGraph + A2A Protocol** | ❌ Single linear loop | ⚠️ Ad-hoc text strings | ❌ Single script loop |
| **Cybersecurity RAG** | **LlamaIndex** (OWASP, CVEs, RoE) | ⚠️ Web search | ❌ None | ⚠️ Static prompts |
| **Persistent User Memory** | **SecWiki** (`preferences.md`, targets) | ❌ Session-only context | ❌ Ephemeral memory | ❌ None |
| **Specialized Skills** | **Modular `SKILL.md`** with auto-detection | ⚠️ Generic tools | ⚠️ Generic tools | ❌ Hardcoded scripts |
| **Air-Gapped Pentesting** | **Ollama, DeepSeek-R1, vLLM & Offline Mock** | ❌ Requires cloud API | ⚠️ Complex custom setup | ❌ Mostly OpenAI only |
| **Terminal UI** | **Textual Dark TUI** (Claude/Open Code aesthetic) | CLI / Plain REPL | Web UI or CLI | Bare terminal |
