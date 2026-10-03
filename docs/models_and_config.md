# LLM Providers & Configuration Guide

ScopeForge provides a model-agnostic agent harness supporting hosted frontier models, local open-source weights, and aggregator proxies.

---

## 1. Switching & Configuring Models

You can configure and switch models in **three ways**:

### A. Via Claude Code / Open Code Style Fast Model Switcher (`/model` or `/config model`)
ScopeForge features an ultra-responsive, keyboard-driven **Model Picker & Config Modal** inspired by Claude Code and Open Code:
- Open instantly with `/model` or `/config model` in the prompt bar.
- **Instant Numeric Shortcuts**:
  - Simply press <kbd>1</kbd>–<kbd>8</kbd> on your keyboard to immediately activate a preset and return to your mission:
    - `[1]` **OpenRouter Free** (`openrouter/free`) — `[FREE] [AUTO]`
    - `[2]` **DeepSeek R1 Free** (`deepseek/deepseek-r1:free`) — `[FREE] [REASONING]`
    - `[3]` **Llama 3.3 70B Free** (`meta-llama/...:free`) — `[FREE] [FAST]`
    - `[4]` **Gemini 2.0 Flash Free** (`google/...:free`) — `[FREE] [FAST]`
    - `[5]` **Claude 3.5 Sonnet** (`anthropic/claude-3.5-sonnet`) — `[FRONTIER]`
    - `[6]` **GPT-4o** (`openai/gpt-4o`) — `[FRONTIER]`
    - `[7]` **Groq Llama 3.3** (`llama-3.3-70b-versatile`) — `[ULTRA-FAST]`
    - `[8]` **Local Ollama** (`llama3:latest`) — `[LOCAL] [OFFLINE]`
- **Arrow Keys & Enter**: Navigate up and down with <kbd>↑</kbd> / <kbd>↓</kbd> and hit <kbd>Enter</kbd> to activate.
- **Custom Model Configuration View**:
  - Press <kbd>9</kbd> or click **Add Custom (9)** to register any model using just **Name**, **Model ID**, **API Key**, and **Base URL**:
    - **Model Name / Alias**: Descriptive name for the model (e.g. `my-deepseek`, `fast-qwen`, `local-vllm`).
    - **Model Identifier / ID**: Target model string (e.g. `deepseek/deepseek-chat`, `gpt-4o`, `qwen/qwen-2.5`).
    - **API Key**: API key (stored securely or leave blank to inherit from environment).
    - **Base URL**: API endpoint URL (e.g. `https://api.deepseek.com/v1`, `https://openrouter.ai/api/v1`, `http://localhost:8000/v1`).
  - Hit <kbd>Enter</kbd> or click **Save & Select**: The custom model is instantly added to your registered models, appears in the OptionList with a `[CUSTOM]` badge, and is immediately activated!

```
╭────────────────────────────────────────────────────────────────────────╮
│ ⚡ Select or Configure Model                                          │
│                                                                        │
│ Navigate with ↑/↓ + Enter, or press 1-9 to select directly:            │
│ ┌────────────────────────────────────────────────────────────────────┐ │
│ │ ● [ACTIVE] [1] OpenRouter Free (openrouter/free)  [FREE] [AUTO]    │ │
│ │   [2] DeepSeek R1 Free (deepseek/deepseek-r1:free) [REASONING]     │ │
│ │   [3] Llama 3.3 70B Free (meta-llama/...:free)     [FAST]          │ │
│ │   [4] Gemini 2.0 Flash Free (google/...:free)      [FAST]          │ │
│ │   [5] Claude 3.5 Sonnet (anthropic/claude-3.5)     [FRONTIER]      │ │
│ │   [6] GPT-4o (openai/gpt-4o)                       [FRONTIER]      │ │
│ │   [7] Groq Llama 3.3 (llama-3.3-70b-versatile)     [ULTRA-FAST]    │ │
│ │   [8] Local Ollama (llama3:latest)                 [LOCAL]         │ │
│ │   [9] my-deepseek (deepseek/deepseek-chat)         [CUSTOM]        │ │
│ │   [10] ➕ Add Custom Model (Name, Model, Key, Base URL)...          │ │
│ └────────────────────────────────────────────────────────────────────┘ │
│ [ Select (Enter) ]           [ Add Custom (9) ]       [ Cancel (Esc) ] │
╰────────────────────────────────────────────────────────────────────────╯
```

### B. Via Fast Inline Slash Commands
Configure anything without leaving the conversation stream:
```text
/model add <name> <model_id> [key] [base_url]    # Add and activate custom model
/model my-deepseek                               # Switch to custom model by name
/config set model deepseek/deepseek-r1:free      # Switch active model on the fly
/config set key sk-or-v1-...                     # Set or update provider API key
/config set temp 0.2                             # Set sampling temperature
/config set mode live                            # Change guardrail mode (plan|artifacts|live)
/config free                                     # Instant switch to zero-cost OpenRouter free tier
/config reset                                    # Reset to default configuration
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
