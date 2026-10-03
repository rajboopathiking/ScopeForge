"""Help modal screen displaying commands, shortcuts, and architecture."""
from __future__ import annotations

from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Label, Markdown, Static

HELP_MARKDOWN = """
# ⚡ SCOPEFORGE CHEAT SHEET & ARCHITECTURE

ScopeForge is an autonomous cybersecurity multi-agent harness inspired by Claude Code and Open Code,
built with **Python Textual**, **LangChain**, **LangGraph**, **LlamaIndex RAG**, and **A2A Protocol**.

---

### ⌨ Keyboard Shortcuts

| Shortcut | Description |
|---|---|
| `F1` | Show this interactive Help modal |
| `F2` | Toggle the Right Sidebar (Agents, Scope, Wiki, RAG, MCP) |
| `F3` | Cycle Execution Mode (`PLAN` ➔ `ARTIFACTS` ➔ `LIVE`) |
| `F4` | Open LLM Wiki & User Preferences Viewer |
| `F5` | Clear conversation history stream |
| `Ctrl+C` | Graceful exit |
| `Tab` | Switch input / sidebar focus |

---

### 🚀 Slash Commands

- `/init`: Initialize `SCOPEFORGE.md` project memory & guidelines in repository root
- `/diff`: Display git diff of working tree changes in the chat stream
- `/commit [msg]`: Commit staged changes or auto-generate a commit message
- `/review`: Autonomous Claude Code code review of git diff for bugs & security flaws
- `/compact`: Compact conversation history to preserve LLM token context
- `/doctor`: Run full system diagnostic check (Python, git, LLM, MCP, audit log)
- `/pr`: Generate formatted GitHub Pull Request description template
- `/model [name]`: Switch active LLM or open interactive model picker
- `/mode <plan|artifacts|live>`: Change execution mode (ScopeGate policy guarded)
- `/agent <name>`: Route next mission to a specialist (`dev`, `recon`, `audit`, `exploit`, `report`)
- `/skill [list|name]`: Discover and toggle specialized skills (`subdomain-recon`, `api-idor-audit`, `cve-triage`)
- `/mcp [list|add|enable|disable]`: Manage Model Context Protocol (MCP) servers (`/mcp add <name> <cmd>`)
- `/config`: Inspect active configuration, model settings, and safety policies
- `/status`: Show full mission status, target info, and agent states
- `/cost`: Show token usage telemetry and session costs
- `/rag <query>`: Query cybersecurity knowledge base (OWASP, CVEs, playbooks)
- `/rag ingest <path>`: Ingest a security advisory or document into the LlamaIndex store
- `/wiki`: Open LLM Wiki and user preferences memory editor
- `/scope [add <target>]`: View or add authorized target domains/IPs to ScopeGate
- `/findings`: Display list of confirmed vulnerabilities
- `/report`: Export full SecOps assessment report with CVSS ratings
- `/clear`: Clear conversation view
- `/quit`: Exit application

---

### 🧠 Skills & MCP Customization

- **Skills Directory**: Place custom skills in `.scopeforge/skills/<skill-name>/SKILL.md`. Include YAML frontmatter with `name`, `description`, and `triggers`. Skills are auto-activated based on user prompts.
- **MCP Servers**: Configure in `.scopeforge/mcp.json` or use `/mcp add <name> <cmd>`. MCP tools are automatically wrapped with ScopeGate security policy validation.

---

### 🛡 Execution Modes & ScopeGate Policy

- **PLAN** (Green): Modeling, hypothesis design, SAST audit. Target contact is strictly prohibited (0 packets).
- **ARTIFACTS** (Yellow): Offline analysis of local PCAPs, HAR logs, source code.
- **LIVE** (Red): Active probing permitted only for declared authorized destinations (e.g. `authorized.example`).
"""


class HelpModal(ModalScreen):
    """Modal screen displaying keyboard shortcuts and command documentation."""

    def compose(self) -> ComposeResult:
        with Vertical(id="modal-dialog"):
            yield Label("⚡ ScopeForge Documentation & Cheat Sheet", id="modal-title")
            with Vertical(id="modal-content"):
                yield Markdown(HELP_MARKDOWN)
            with Horizontal(id="modal-buttons"):
                yield Button("Close (Esc)", variant="primary", id="btn-close", classes="modal-btn")

    def on_button_pressed(self, event: Button.Pressed):
        if event.button.id == "btn-close":
            self.dismiss()

    def on_key(self, event):
        if event.key in ("escape", "f1"):
            self.dismiss()
