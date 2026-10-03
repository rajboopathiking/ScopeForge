# LLM Providers & Configuration Guide

ScopeForge provides a model-agnostic agent harness supporting hosted frontier models, local open-source weights, and aggregator proxies.

---

## 1. Switching & Configuring Models

You can configure and switch models in **three ways**:

### A. Via In-Terminal TUI Configuration Center (`/model` or `/config model`)
ScopeForge features an interactive, keyboard-driven **LLM Provider & Model Configuration Center** directly inside the Terminal UI:
- Open with `/model` or `/config model` in the prompt bar.
- **Tab 1 ("📋 Active Models & Quick Switch")**:
  - Displays a live table of all registered providers (`Active`, `Name`, `Provider`, `Model ID`, `Temperature`).
  - Select with <kbd>↑</kbd> / <kbd>↓</kbd> and click **`Activate Selected`** or press <kbd>Enter</kbd>.
- **Tab 2 ("⚙️ Configure Provider in UI")**:
  - **Quick Presets (One-Click Auto-Fill)**:
    - `[OpenRouter Free]` (`openrouter/free`)
    - `[DeepSeek R1 Free]` (`deepseek/deepseek-r1:free`)
    - `[Llama 3.3 Free]` (`meta-llama/llama-3.3-70b-instruct:free`)
    - `[Gemini 2.0 Free]` (`google/gemini-2.0-flash-exp:free`)
    - `[Claude 3.5]` (`anthropic/claude-3.5-sonnet`)
    - `[Local Ollama]` (`llama3:latest` at `http://localhost:11434`)
  - **Custom Form Inputs**:
    - **Provider Platform**: Dropdown with `openrouter`, `anthropic`, `openai`, `ollama`, `groq`, `custom`.
    - **Configuration Name**: Identifier for the provider entry.
    - **Model ID**: Any model identifier (e.g. `openrouter/free`, `deepseek/deepseek-r1:free`).
    - **API Key**: Secure password input (masked for screen privacy; optional if set in ENV).
    - **API Base URL**: Customizable endpoint (e.g. `https://openrouter.ai/api/v1` or local vLLM `http://localhost:8000/v1`).
    - **Temperature & Max Tokens**: Adjust sampling temperature and token limits.
  - **Action Buttons**:
    - **`💾 Save & Activate in UI`**: Saves the configuration to `.scopeforge/providers.yaml` and immediately activates it for your live session.
    - **`Save to Config Only`**: Registers the model without interrupting your current session.

```
╭─────────────────────────────────────────────────────────────────────────────────────────────╮
│ ⚡ LLM Provider & Model Configuration Center                                               │
│                                                                                             │
│  [📋 Active Models & Quick Switch]   [⚙️ Configure Provider in UI]                          │
│                                                                                             │
│  🚀 Quick Presets (Click to Auto-Fill):                                                     │
│  [ OpenRouter Free ]  [ DeepSeek R1 Free ]  [ Llama 3.3 Free ]  [ Gemini 2.0 Free ] ...     │
│                                                                                             │
│  Provider Platform: [ OpenRouter (openrouter.ai)                                          ] │
│  Configuration Name: [ openrouter-free                                                    ] │
│  Model ID:           [ openrouter/free                                                    ] │
│  API Key:            [ ••••••••••••••••••••••••••••••••                                   ] │
│  API Base URL:       [ https://openrouter.ai/api/v1                                       ] │
│  Temperature: [ 0.2 ]                    Max Tokens: [ 4096 ]                               │
│                                                                                             │
│  [ 💾 Save & Activate in UI ]       [ Save to Config Only ]       [ Close (Esc) ]           │
╰─────────────────────────────────────────────────────────────────────────────────────────────╯
```

### B. Via Fast CLI / Slash Commands
```text
/model free                                      # Activate OpenRouter Free tier
/model openrouter deepseek/deepseek-r1:free      # Mount DeepSeek R1 Free on the fly
/model openrouter meta-llama/llama-3.3-70b:free  # Mount Llama 3.3 Free on the fly
/model claude-3-7-sonnet                         # Direct Anthropic Claude 3.7
/model gpt-4o                                    # Direct OpenAI GPT-4o
/model ollama-llama3                             # Local offline Ollama
```

### C. Via Configuration File (`.scopeforge/providers.yaml`)
Configurations saved through the UI are automatically persisted to `.scopeforge/providers.yaml`:
```yaml
active: openrouter-free
providers:
  openrouter-free:
    name: openrouter-free
    provider: openrouter
    model: openrouter/free
    api_base: https://openrouter.ai/api/v1
    temperature: 0.2
    max_tokens: 4096
    streaming: true
    extra_headers:
      HTTP-Referer: https://github.com/rajboopathiking/ScopeForge
      X-Title: ScopeForge Agent Harness
```

---

## 2. Supported LLM Providers

### 1. Anthropic (Claude 3.7 Sonnet / Claude 3.5 Sonnet)
Set your API key in the environment or `.env`:
```bash
export ANTHROPIC_API_KEY="sk-ant-..."
```
ScopeForge auto-detects `ANTHROPIC_API_KEY` and defaults to Claude 3.7 Sonnet.

### 2. OpenAI (GPT-4o / GPT-4o-mini / o1)
```bash
export OPENAI_API_KEY="sk-..."
```
Switch via `/model gpt-4o`.

### 3. Local Ollama (Air-gapped & Offline)
Ensure Ollama is running (`ollama serve`):
```bash
ollama run llama3:latest
# or:
ollama run deepseek-r1:latest
```
Switch via `/model ollama-llama3` or `/model ollama-deepseek-r1`.
Ollama communicates via `http://localhost:11434/v1`.

### 4. OpenRouter (Access 100+ Models)

Set your OpenRouter API key:
```bash
export OPENROUTER_API_KEY="sk-or-v1-..."
```

#### A. Free Tier Models on OpenRouter
OpenRouter provides models that are completely free to use with zero credit consumption:
- **`/model openrouter/free`** (or **`/model free`**) — OpenRouter Free Router (`openrouter/free`)
- **`/model openrouter-free-deepseek`** — DeepSeek R1 Free (`deepseek/deepseek-r1:free`)
- **`/model openrouter-free-llama`** — Meta Llama 3.3 70B Free (`meta-llama/llama-3.3-70b-instruct:free`)
- **`/model openrouter-free-gemini`** — Google Gemini 2.0 Flash Free (`google/gemini-2.0-flash-exp:free`)

#### B. Premium Preset Models
ScopeForge also includes presets for top frontier models:
- `/model openrouter-claude` — Anthropic Claude 3.5 Sonnet
- `/model openrouter-deepseek-r1` — DeepSeek R1 (reasoning model)
- `/model openrouter-deepseek-v3` — DeepSeek V3 (general chat)
- `/model openrouter-llama3` — Meta Llama 3.3 70B Instruct
- `/model openrouter-qwen` — Qwen 2.5 Coder 32B Instruct

#### C. Dynamic On-the-Fly Switching
You can point to **any** model cataloged on [OpenRouter](https://openrouter.ai/models) in real time:
```text
/model openrouter deepseek/deepseek-r1
/model openrouter google/gemini-2.0-flash-001
/model openrouter meta-llama/llama-3.3-70b-instruct
/model openrouter mistralai/mistral-large-2411
/model openrouter cohere/command-r-plus-08-2024
```
ScopeForge will instantly register the provider, set its endpoint to `https://openrouter.ai/api/v1`, and activate it for your session.

---

## 3. General LLM Q&A Mode

While ScopeForge has high-performance cybersecurity agents and ScopeGate protection, it also serves as a **first-class general AI coding & knowledge assistant**.

### How Routing Works
- **General Queries**: When your question is about software engineering, programming, computer science, mathematics, architecture, or general knowledge, the **Supervisor** directly responds using the full reasoning capabilities of your active model (Claude 3.5 Sonnet, DeepSeek R1, GPT-4o, etc.).
- **Security Operations**: Specialized agent nodes (`Recon`, `Audit`, `Exploit`, `Report`) and security tools are only activated when your prompt targets reconnaissance, port scans, SAST code review, CVE advisories, or exploit verification.

### Examples of General Q&A:

```text
❯ Explain the architectural differences between event-driven architecture and polling.
```

```text
❯ Write a Python snippet that implements a thread-safe LRU cache using collections.OrderedDict.
```

```text
❯ What are the trade-offs of using B-Tree vs LSM-Tree for database storage engines?
```

```text
❯ How do I configure Nginx to handle WebSocket upgrades with proper timeout handling?
```

- **Conversation Memory**: Conversation context is maintained across turns.
- **Project Guidelines**: If a `SCOPEFORGE.md` or `CLAUDE.md` file exists in your project root, the supervisor automatically adheres to your tech stack and coding conventions.
- **Context Compaction**: If your conversation grows long, type `/compact` to summarize history and preserve token budget.


## 3. Runtime Configuration

Inspect your active configuration at any time using:
```text
/config
```
Output:
- Active model & provider
- Temperature & max token limits
- Execution safety mode (`PLAN`, `ARTIFACTS`, `LIVE`)
- Authorized ScopeGate targets
- Active skills & RAG index status

Check session token counters and cost telemetry:
```text
/cost
```
Show overall mission status:
```text
/status
```
