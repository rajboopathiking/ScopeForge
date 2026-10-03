# ScopeForge vs. Other Agent Harnesses for Cybersecurity Tasks

How ScopeForge compares against generic AI coding agents, generic multi-agent frameworks, and first-generation security AI wrappers.

---

## 1. Executive Summary

Most existing AI agent harnesses were designed either for **generic software engineering** (Claude Code, Open Code, Aider, OpenHands) or **generic multi-agent conversation** (CrewAI, AutoGen). When applied to cybersecurity tasks (penetration testing, bug bounty triage, SAST code review, perimeter reconnaissance), generic harnesses suffer from severe domain deficiencies:

- **Lack of Authorization Boundaries (Scope Blindness)**: They have no concept of Rules of Engagement (RoE). A generic agent given an exploratory prompt can easily probe unauthorized `.gov`, `.mil`, or third-party cloud infrastructure, creating severe legal liabilities.
- **Speculative Hallucinations vs. Falsifiable Evidence**: Generic agents "hallucinate" vulnerabilities by speculating from code snippets without proving baseline vs. payload differentials.
- **Uncontrolled Destructive Tooling**: Generic agents lack security-aware middleware to intercept destructive commands (`rm -rf`, dropping tables, denial-of-service payloads).
- **No Cryptographic Proof Ledger**: Security assessments require reproducible proof-of-concept (PoC) artifacts and tamper-evident audit logs for client and bug bounty reports.

**ScopeForge was designed from the ground up specifically as a cybersecurity agent harness**, combining a modern terminal UI (Claude Code/Open Code style) with **LangGraph multi-agent orchestration**, **ScopeGate policy enforcement**, **LlamaIndex RAG**, **persistent LLM Wiki memory**, **A2A protocol**, and **SHA256 evidence ledgers**.

---

## 2. Comparative Matrix

| Capability | ScopeForge | Generic Coding Agents (Claude Code, Aider, OpenHands) | Generic Multi-Agent (CrewAI, AutoGen) | 1st-Gen Sec Wrappers (PentestGPT, HackGPT) |
|---|:---:|:---:|:---:|:---:|
| **Rules of Engagement (ScopeGate)** | **Built-in Policy Enforcer** (`PLAN`, `ARTIFACTS`, `LIVE`) | None (blind host access) | None (unconstrained) | Manual / Prompt only |
| **Out-of-Scope Blocking** | **Strict CIDR / Domain Matcher** (regex + allowlist) | No boundary check | No boundary check | Weak / None |
| **Evidence Preservation** | **Cryptographic SHA256 Evidence Store** | Transient terminal output | None | Raw text dumps |
| **Verification Standard** | **Falsifiable Differential** (Baseline vs. Test) | Speculative text | Speculative chat | Basic chat advice |
| **Multi-Agent Architecture** | **LangGraph StateGraph + A2A Protocol** | Single agent / linear | Multi-agent (unstructured) | Single script loop |
| **Inter-Agent Protocol** | **Standardized A2A Protocol** (Signed envelopes) | None | Ad-hoc text messages | None |
| **Cybersecurity Knowledge Base** | **LlamaIndex RAG** (OWASP, CVEs, RoE) | Generic web search | None / Generic vector DB | Hardcoded prompts |
| **Persistent User Memory** | **LLM Wiki** (`preferences.md`, targets, playbooks) | Session context only | Ephemeral memory | None |
| **Specialized Skills** | **Modular `SKILL.md`** with auto-detection triggers | Static prompts | Generic tools | Static prompts |
| **MCP (Model Context Protocol)** | **Built-in with Untrusted Description Guards** | Partial / Tool-based | Custom coding required | None |
| **Air-Gapped / Local LLMs** | **Native Ollama, DeepSeek-R1, vLLM & Offline Mock** | Requires cloud API | Requires setup | Mostly OpenAI only |
| **Terminal UI (TUI)** | **Textual Dark TUI** (Claude/Open Code aesthetic) | CLI / Plain REPL | Web UI or CLI | Bare terminal |
| **Human-in-the-Loop** | **Modal Approval Gate** for sensitive tools | Prompt confirmation | Python callbacks | None |

---

## 3. Why ScopeForge is Superior for Cybersecurity

### 1. The ScopeGate Policy Enforcer (Safety & Legality)
In penetration testing and bug bounty engagements, contacting an out-of-scope IP is an immediate disqualification or criminal violation (CFAA / Computer Misuse Act).
- Generic agents will blindly follow subdomains or third-party links found in HTTP responses.
- **ScopeForge ScopeGate**:
  - In `PLAN` mode: Zero network packets are permitted. Only threat modeling, hypothesis design, and local SAST audits run.
  - In `ARTIFACTS` mode: Only supplied local files (HAR captures, PCAPs, source code) are inspected.
  - In `LIVE` mode: Tool requests are dynamically parsed for destination hosts and validated against the authorized target list. Any contact outside authorized boundaries (or matching `*.gov`, `*.mil`) is blocked at the middleware layer before touching the network.

### 2. Falsifiable Hypotheses Instead of Hallucinations
Generic LLMs frequently produce false positives (e.g. claiming a website has SQL injection merely because a parameter is named `id`).
- ScopeForge enforces a **scientific, falsifiable methodology**:
  1. Formulate a testable assertion ($H_1$).
  2. Measure baseline state ($S_0$: HTTP status, body length, header hash).
  3. Apply isolated test payload ($S_1$).
  4. Measure differential ($\Delta = S_1 - S_0$).
  5. The finding is marked `CONFIRMED` only if an anomalous differential is reproduced; otherwise, it is falsified and discarded.

### 3. Tamper-Evident SHA256 Audit Trail
Security assessments must be legally defensible and verifiable:
- Every prompt, model response, tool execution, and inter-agent message is recorded in an append-only JSONL ledger (`.scopeforge/audit.jsonl`).
- Each entry contains a cryptographic hash chained to the previous entry (`prev_hash`), ensuring that the audit history cannot be altered after the fact.
- All HTTP request/response artifacts are hashed with SHA256 and archived in `.scopeforge/evidence/`.

### 4. Specialized Multi-Agent Team (LangGraph + A2A)
Rather than a single monolithic prompt, ScopeForge deploys specialized agents orchestrated via a compiled LangGraph state machine:
- **`Supervisor`**: Interprets mission, consults LlamaIndex RAG and Wiki preferences, coordinates subtasks.
- **`ReconAgent`**: Maps network perimeter, inspects headers, fingerprints services.
- **`AuditAgent`**: Analyzes code for OWASP Top 10 vulnerabilities and cross-references CVE databases.
- **`ExploitAgent`**: Formulates safe, non-destructive PoCs and executes hypothesis testing.
- **`ReportAgent`**: Generates executive summaries, CVSS v3.1 vector strings, and remediation plans.
- **Agent-to-Agent (A2A) Protocol**: Agents communicate through structured, cryptographically signed messages (`TASK_DELEGATION`, `EVIDENCE_SHARING`, `HANDOVER`) visible live in the TUI stream and sidebar.

### 5. Confidentiality & Local LLM Support (Air-Gapped Pentesting)
Security researchers frequently operate under strict Non-Disclosure Agreements (NDAs) that prohibit sending client source code, credentials, or network maps to third-party cloud APIs:
- ScopeForge provides native support for **local models** via Ollama (`llama3`, `deepseek-r1`, `qwen2.5-coder`) and custom OpenAI-compatible vLLM endpoints.
- Includes a built-in high-fidelity **Mock SecOps** provider for offline testing and demos without any external network traffic or API keys.

### 6. Interactive Terminal Experience (Claude Code / Open Code Style)
Unlike clunky script wrappers, ScopeForge provides a modern Textual TUI:
- **Status Header**: Displays brand, active model, safety mode badge, target scope, active agent, and token/cost counters.
- **Collapsible Tool Cards**: Shows tool invocations, parameters, ScopeGate authorization status, and live output.
- **Live A2A Feed**: Shows real-time inter-agent delegation and evidence handover.
- **Interactive Modals**: Keyboard-driven modals for Help (<kbd>F1</kbd>), Model Picker (<kbd>/model</kbd>), Operator Approval, and Wiki Editor (<kbd>F4</kbd>).
- **Extensible Skills**: Add new security skills simply by dropping a `SKILL.md` into `.scopeforge/skills/`.
