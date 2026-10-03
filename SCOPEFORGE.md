# SCOPEFORGE.md — Project Memory & Agent Guidelines

## 1. Project Overview & Architecture
ScopeForge is an autonomous developer and cybersecurity multi-agent harness inspired by Claude Code and Open Code.
It combines a reactive Python Textual terminal interface (TUI) with a LangGraph multi-agent network, custom LLM providers, LlamaIndex RAG, persistent LLM Wiki memory, and ScopeGate cybersecurity Rules of Engagement (RoE).

- **TUI Frontend**: `services/engine/src/scopeforge_engine/tui/`
- **LangGraph Multi-Agent Team**: `services/engine/src/scopeforge_engine/agents/`
  - `Supervisor`: General coding assistant, system architect, and mission coordinator.
  - `DevAgent`: File inspection (`view_file`), code editing (`edit_file`), file creation (`write_file`), file finding (`glob_files`), regex search (`grep_search`), and git operations (`git_diff_tool`, `git_status_tool`).
  - `ReconAgent`: Perimeter mapping (`recon_port_scan`, `web_surface_probe`).
  - `AuditAgent`: Static application security testing (`sast_code_audit`, `cve_advisory_search`).
  - `ExploitAgent`: Falsifiable verification & PoC runner (`falsifiable_poc_runner`, `evidence_recorder`).
  - `ReportAgent`: Synthesizes findings with CVSS v3.1 scores and prioritized remediation.
- **Middleware Pipeline**: `services/engine/src/scopeforge_engine/middleware/`
  - ScopeGate RoE enforcement (`PLAN`, `ARTIFACTS`, `LIVE`).
  - Tamper-evident SHA-256 audit ledger (`.scopeforge/audit.jsonl`).
  - Sensitive credential & token redaction.
- **Skills**: Modular `SKILL.md` playbooks located in `skills/` or `.scopeforge/skills/`.
- **RAG & Wiki**: LlamaIndex vector store (`/rag`) and Markdown SecWiki (`/wiki`).

---

## 2. Build, Test & Verification Commands
- **Run Tests**:
  ```bash
  python -m pytest services/engine/tests -q
  ```
- **Launch TUI**:
  ```bash
  python scopeforge_tui.py
  # or
  uv run python scopeforge_tui.py
  ```
- **Verify SBOM & Provenance**:
  ```bash
  python scripts/sbom.py
  ```

---

## 3. Development & Safety Rules
1. **ScopeGate Policy First**: Never execute external network requests against un-authorized targets.
2. **Deterministic Evidence**: All vulnerability findings must be falsifiable and cryptographically hashed before reporting.
3. **Secret Redaction**: Always redact API keys, tokens, and passwords in tool logs and chat outputs.
4. **Code Quality**: Maintain clean async patterns, type annotations, and ensure all unit tests pass before committing.
