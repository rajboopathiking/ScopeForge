# ScopeForge Terminal UI (Claude Code & Open Code style) Guide

The ScopeForge Terminal UI is a keyboard-first, high-density terminal interface designed for autonomous cybersecurity operations. It is built natively on **Python Textual**, **LangChain**, **LangGraph**, **LlamaIndex RAG**, and **A2A Protocol**.

---

## 1. Interface Layout

```
╭─────────────────────────────────────────────────────────────────────────────────────────────╮
│ ⚡ SCOPEFORGE   claude-3-7-sonnet   PLAN   authorized.example   Supervisor   1,420 tok      │
├─────────────────────────────────────────────────────────────┬───────────────────────────────┤
│                                                             │ [Skills] [Agents] [Scope] ... │
│  ╭───────────────────────────────────────────────────────╮  │ • subdomain-recon             │
│  │ ⚡ SCOPEFORGE — Cybersecurity Multi-Agent Terminal     │  │ • api-idor-audit              │
│  ╰───────────────────────────────────────────────────────╯  │ • cve-triage                  │
│                                                             │ • reverse-proxy-bypass        │
│  ❯ You: Run a security audit on authorized.example          │                               │
│                                                             │ ⇄ A2A Message Stream          │
│  🤖 [SupervisorAgent]                                       │ 15:32:01 Sup -> Recon [TASK]  │
│  Validating scope boundaries and dispatching ReconAgent...  │ 15:32:02 Recon -> Audit [RES] │
│                                                             │                               │
│  ⚙ Tool Call: recon_port_scan(target="authorized.example")  │ ScopeGate Policy              │
│  -> [SUCCESS] Discovered ports: 80, 443, 8080               │ - Mode: PLAN                  │
│                                                             │ - Auth: authorized.example    │
│  ⇄ A2A Protocol: ReconAgent -> AuditAgent [EVIDENCE_SHARING]│                               │
│                                                             │ Findings Ledger               │
│  🚨 VULNERABILITY FOUND: SQL Injection (CVSS 9.8)           │ [CRITICAL] FIND-001 (CVSS 9.8)│
│                                                             │                               │
├─────────────────────────────────────────────────────────────┴───────────────────────────────┤
│ ❯ Ask cybersecurity agents, run recon, audit code, or type / for commands...                │
│ Commands: /help  /model  /mode  /agent  /skill  /mcp  /config  /rag  /wiki  /status  /clear │
╰─────────────────────────────────────────────────────────────────────────────────────────────╯
```

---

## 2. Status Header Badges

Located at the very top of your terminal:
- **`⚡ SCOPEFORGE`**: System brand badge.
- **Model Pill**: Active LLM (`claude-3-7-sonnet`, `gpt-4o`, `ollama-llama3`, `mock-secops`).
- **Mode Pill**: Safety execution mode:
  - `PLAN` (Green): Modeling and SAST only. Strictly 0 network packets sent.
  - `ARTIFACTS` (Yellow): Offline inspection of supplied HAR logs, PCAPs, code.
  - `LIVE` (Red): Active network probing, strictly bounded to authorized targets.
- **Scope Pill**: Current primary authorized target domain or CIDR.
- **Agent Pill**: Currently active agent specialist (`Supervisor`, `Recon`, `Audit`, `Exploit`, `Report`).
- **Cost / Tokens Pill**: Accumulated token count and estimated session cost.

---

## 3. Keyboard Shortcuts

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
| <kbd>Tab</kbd> | — | **Focus Switch** | Switch navigation focus between prompt and sidebar |
| <kbd>Esc</kbd> | — | **Close Modal** | Close any open modal screen |

---

## 4. Slash Commands Reference

| Command | Usage | Description |
|---|---|---|
| **`/init`** | `/init` | Initialize `SCOPEFORGE.md` (or `CLAUDE.md`) project memory and guidelines in repository root |
| **`/diff`** | `/diff` | Display git diff of working tree changes in the chat stream with syntax highlighting |
| **`/commit`** | `/commit [message]` | Commit staged changes or auto-generate a conventional commit message |
| **`/review`** | `/review` | Autonomous Claude Code style review of git changes for bugs, logic flaws, and security risks |
| **`/compact`** | `/compact` | Compact conversation history to free up LLM context window tokens |
| **`/doctor`** | `/doctor` | Run full system health & diagnostic check (Python, git, LLM, MCP, audit log) |
| **`/pr`** | `/pr` | Generate formatted GitHub Pull Request description template |
| `/help` | `/help` | Open the interactive documentation cheat sheet |
| `/model` | `/model [name]` | Switch LLM or open interactive model picker table |
| `/mode` | `/mode <plan\|artifacts\|live>` | Change ScopeGate policy execution mode |
| `/agent` | `/agent <name>` | Direct task to `dev`, `recon`, `audit`, `exploit`, `report` |
| `/skill` | `/skill [list\|<name>]` | Discover or toggle specialized agent skills |
| `/mcp` | `/mcp [list\|add\|enable]` | Manage Model Context Protocol external servers |
| `/config` | `/config` | Inspect runtime model parameters, safety modes, and rules |
| `/status` | `/status` | Display full mission status and agent team health |
| `/cost` | `/cost` | View token usage breakdown and session costs |
| `/rag` | `/rag <query>` | Query LlamaIndex cybersecurity store (OWASP, CVEs, RoE) |
| `/rag ingest` | `/rag ingest <path>` | Ingest a local advisory or document into RAG |
| `/wiki` | `/wiki` | Open interactive persistent memory editor |
| `/scope` | `/scope [add <target>]` | View or add target domains to ScopeGate allowlist |
| `/findings` | `/findings` | View all discovered vulnerabilities and CVSS scores |
| `/report` | `/report` | Compile and export full SecOps report |
| `/clear` | `/clear` | Clear chat log |
| `/quit` | `/quit` | Exit ScopeForge |

---

## 4.1 Claude Code Developer Toolset

ScopeForge provides the full developer toolset found in Claude Code and Open Code:

- **`view_file(file_path, start_line, end_line)`**: Inspect code and configuration files with 1-indexed line numbers.
- **`edit_file(file_path, target_content, replacement_content)`**: Surgical, exact-match code refactoring and replacements.
- **`write_file(file_path, content, overwrite)`**: Create new files or overwrite existing files safely.
- **`glob_files(pattern, directory)`**: Fast file pattern matching across the repository tree (ignoring `.git`, `.venv`, `node_modules`).
- **`grep_search(query, directory, file_pattern)`**: Regex and keyword search across file contents with matching lines and file paths.
- **`git_diff_tool(staged)`**: Native git diff inspection for working tree or staged changes.
- **`git_status_tool()`**: Repository status, active branch, and untracked files.
- **`git_commit_tool(message)`**: Safe commit execution.

---

## 4.2 In-Terminal Model & Provider Configuration Center

Launch via `/model` or `/config model`. It is a single modal with two views — **selection list first, custom form on demand** (never custom-only):

- **View 1: 📋 Model Selection List** (default):
  - Filter box (`Type to filter models...`), live-filtered `OptionList` of featured presets + `[STOCK]` + `[CUSTOM]` models, ending with `[+] Add Custom Model...`.
  - <kbd>1</kbd>–<kbd>9</kbd> selects from the first 9 visible rows; deeper rows show `[·]` and need <kbd>↑</kbd>/<kbd>↓</kbd> + <kbd>Enter</kbd>. Buttons: **`Select (Enter)`**, **`Add Custom (+)`**, **`Cancel (Esc)`**.
- **View 2: ⚙️ Add Custom Model** (via `Add Custom (+)` button, `[+]` row, or <kbd>+</kbd>/<kbd>c</kbd>):
  - **Interactive Inputs**: Alias/Name, Provider Platform, Model ID*, API Key* (`env:VAR` supported), API Base URL*, Temperature, Max Tokens.
  - **Instant Buttons**:
    - **`Save & Select`**: Saves to `.scopeforge/providers.yaml`, badges `[CUSTOM]`, and switches the active session model immediately (warns `⚠️ Running offline` when no usable key/endpoint).
    - **`Test`**: Validates without saving or touching disk.
    - **`Back`**: Returns to the selection list.

---

## 5. Sidebar Tabs

1. **Skills**: Discovered skills from `.scopeforge/skills/`, descriptions, and trigger keywords.
2. **Agents & A2A**: Multi-agent team roster and real-time cryptographically signed A2A message bus.
3. **Scope & Policy**: ScopeGate boundary allowlist, forbidden patterns (`*.gov`, `*.mil`), and active mode.
4. **Wiki & Memory**: User preferences (`preferences.md`), targets, and pentest playbooks.
5. **RAG Store**: LlamaIndex document index counter, status, and interactive query preview.
6. **MCP Protocol**: Connected Model Context Protocol servers and tool status.
7. **Findings**: Verified vulnerability ledger with CVSS v3.1 scores and severity badges.
