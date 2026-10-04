# ScopeForge — authorized bug-bounty research & deliberate practice harness

> Working name. Independent, open-source, model-agnostic CLI/TUI agent harness.
> See `plan.md` for the implementation-ready product + engineering plan.

**Status:** All phases implemented (0–9) — see `docs/` and `plan.md` §13
**Languages:** TypeScript (UI, `apps/terminal`) + Python (engine, `services/engine`)
**License:** Apache-2.0
**Execution context:** plan only; no target systems contacted (labs are local fixtures).

## Promise

Turn an authorized program + researcher-controlled fixtures into a bounded queue
of experiments, preserve evidence, challenge weak conclusions, teach reproducible reports.

Core object: **authorized engagement containing falsifiable test cases + evidence** — not a chat.

## Architecture (two local processes)

1. **TypeScript CLI/TUI** (`apps/terminal`) — terminal experience, streaming display,
   approvals, diffs, command parsing. Never contacts targets directly.
2. **Python engine** (`services/engine`) — orchestration, model adapters, policy/scope
   gate, tool execution, evidence, run state, coaching, reports. Only component allowed
   to execute a security tool or contact a target.
3. **JSON-RPC 2.0 over newline-delimited stdio** (`packages/protocol-schema` is source
   of truth, generates `packages/protocol-ts` + Python models). No remote transport in MVP.

## Execution modes (badge always visible)

| Mode | Target contact |
|---|---|
| `plan` | Never — policy parsing, modeling, case design from supplied docs |
| `artifacts` | Never — analyze supplied HAR/code/responses/screenshots |
| `live` | Only after policy gate, bounded to exact authorized destinations |

## Quickstart

> Full setup with prerequisites, layout, providers, labs, verification, and troubleshooting: **[docs/setup.md](docs/setup.md)** — single copy-paste block in `docs/README.md`.

```bash
pnpm install --frozen-lockfile
uv sync --python 3.12 --all-extras --all-packages
pnpm generate:protocol
pnpm lint && pnpm test && uv run pytest && node scripts/generate-protocol.mjs --check
```

### Modern Terminal UI (Claude Code & Open Code style)

ScopeForge is available as a pip/PyPI package and launches directly from your command line:

```bash
# Install via pipx / pip:
pip install scopeforge
# Or run instantly with zero installation via uv:
uvx scopeforge

# Launch the interactive Claude Code style TUI:
scopeforge
# Or shorthand:
sf
```

**Key Capabilities:**
- **Sleek Claude Code / Open Code Look & Feel**: Modern dark theme, status pills (`[Model]`, `[Mode]`, `[Scope]`, `[Tokens]`), collapsible tool cards, markdown rendering, and finding badges.
- **Autonomous LangChain Tool Harness**: Real iterative tool calling loop (`bind_tools`) for bash execution (`bash_cli`), live web search (`google_web_search`), code inspection (`view_file`), surgical editing (`edit_file`), and dynamic security auditing.
- **In-Terminal Model Switcher & Setup (<kbd>Ctrl+M</kbd>)**: Native `ContentSwitcher` model picker modal with live preset filtering and custom API endpoint setup (no duplicate screens or phantom focus).
- **Zero-Duplicate Terminal Paste**: Two-layer paste debouncing and doubled string collapsing (`deduplicate_doubled_text`) eliminates terminal duplicate paste artifacts.
- **OpenRouter Free Tier Integration**: Out-of-the-box zero-cost models (`openrouter/free`, `openrouter/free-nemotron`, `groq-llama3`, `ollama-llama3`).
- **Modern `Ctrl+[key]` Keybindings**: Standard terminal shortcuts (`Ctrl+M`, `Ctrl+H`, `Ctrl+B`, `Ctrl+O`, `Ctrl+W`, `Ctrl+Y`, `Ctrl+T`, `Ctrl+L`, `Ctrl+Q`).
- **Claude Code Slash Commands**: Full command suite including `/init` (`SCOPEFORGE.md` project memory), `/diff`, `/commit`, `/review`, `/search`, `/bash`, `/mcp`, `/skill`, `/compact`, `/doctor`, `/pr`, and `/cost`.
- **LangGraph Multi-Agent Team**: `Supervisor`, `DevAgent`, `ReconAgent`, `AuditAgent`, `ExploitAgent`, `ReportAgent`, communicating via cryptographic A2A Protocol bus.
- **LlamaIndex RAG & LLM Wiki**: Embedded cybersecurity knowledge base with `/rag` and persistent memory with `/wiki`.
- **Model Context Protocol (MCP)**: Discover, register, and run tools from any MCP server via `/mcp`.

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

**Documentation:**
- **[Master Manual (docs/SCOPEFORGE_MANUAL.md)](docs/SCOPEFORGE_MANUAL.md)**: Exhaustive manual covering TUI, LangGraph, RAG, Wiki, Skills, MCP, RoE, and Benchmarks.
- **[TUI Guide (docs/tui_guide.md)](docs/tui_guide.md)**: Full terminal UI navigation, layout, modern keybindings, slash commands, and in-terminal configuration center.
- **[Models & Config (docs/models_and_config.md)](docs/models_and_config.md)**: In-terminal configuration center, OpenRouter Free models, provider setup, hot-swapping, and general Q&A.
- **[Specialized Skills (docs/skills.md)](docs/skills.md)**: Creating custom skills (`SKILL.md`), YAML frontmatter, triggers, and auto-activation.
- **[MCP Customization (docs/mcp.md)](docs/mcp.md)**: Connecting MCP stdio servers, permissions, and tool execution with ScopeGate.
- **[Why ScopeForge is Better for Cybersecurity (docs/cybersecurity_harness_comparison.md)](docs/cybersecurity_harness_comparison.md)**: Architectural comparison against generic agents (Claude Code, CrewAI, PentestGPT).

CLI (every scriptable command supports `--json` / `--no-color`):

```bash
scopeforge init && scopeforge doctor
scopeforge run start --mode plan            # or artifacts; live is Phase-5-gated
scopeforge run list
scopeforge case new --run run_0001 --check T02.1 --asset https://authorized.example \
  --hypothesis "..." --expected-rule "..." --baseline "..." --changed "one variable" \
  --observable "..." --stopping "..." --permission P-2026-09-15
scopeforge tui --run run_0001              # plain backend (ADR-04); --plain/--once for pipes
```

Providers (Phase 3: OpenAI-compatible + DeepSeek preset + Anthropic-compatible + mock;
Phase 8: native Gemini/Ollama, OpenRouter/Azure presets, optional LiteLLM bridge):

```bash
scopeforge provider add --name work --type deepseek --credential env:DEEPSEEK_API_KEY
scopeforge provider test work              # presence + declared caps + live reachability
scopeforge models list
scopeforge models inspect deepseek:deepseek-reasoner
# Gemini / Ollama / presets via same add path:
# scopeforge provider add --name g --type gemini --credential env:GEMINI_API_KEY
# scopeforge provider add --name local --type ollama
```

See `docs/providers.md` (capability matrix, credentials, privacy),
`docs/adr/ADR-07-provider-architecture.md` (why the core stays framework-free),
and `docs/tools.md` (MCP trust, tool-wrapper SDK, HAR/Burp import).

Run engine standalone (stdio JSON-RPC):

```bash
uv run scopeforge-engine --stdio
node scripts/launcher.mjs --engine services/engine/src/scopeforge_engine/server.py
uv run python scripts/sbom.py  # -> sbom.json + provenance.json
```

See `docs/packaging.md` for install, launcher verification, version matrix,
SBOM/provenance/signing, and offline install.

Minimal transcript (each line = one JSON-RPC message):

```jsonl
{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocol_version":"0.1.0","client":"terminal/0.1.0"}}
{"jsonrpc":"2.0","id":2,"method":"capabilities","params":{}}
{"jsonrpc":"2.0","id":3,"method":"health","params":{}}
```

See `docs/protocol.md`, `docs/architecture.md`, `docs/threat-model.md`,
`docs/authorization.md`, and `evals/golden/` for the artifact-only T02 slice.

## Safe use

- Only test assets you are explicitly authorized to test. Empty scope = unknown, never unrestricted.
- `plan` / `artifacts` modes have zero target egress by construction (integration-tested).
- Live testing of public programs is beta-gated behind policy + egress + audit release gates.
- See `SECURITY.md`.

## Repo layout

See `plan.md` §12 and `docs/setup.md` §3. Key dirs: `apps/terminal`, `services/engine`,
`packages/protocol-schema`, `schemas/`, `docs/adr/`, `evals/golden/`, `tests/`, `labs/`.

## Docs

- **[docs/README.md](docs/README.md)** — index (setup → architecture → threat model → …)
- **[docs/setup.md](docs/setup.md)** — prerequisites, quickstart, verification, troubleshooting
- `docs/architecture.md`, `docs/threat-model.md`, `docs/authorization.md`, `docs/protocol.md`, `docs/development.md`
- `docs/providers.md`, `docs/tools.md`, `docs/evidence.md`, `docs/methodology.md`, `docs/learning.md`, `docs/packaging.md`
- `docs/adr/` — ADR-01 … ADR-07
- `SECURITY.md`, `CONTRIBUTING.md` — reporting + contribution flow
- `plan.md` — 1312-line implementation-ready spec (single source)
