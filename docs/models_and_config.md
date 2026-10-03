# LLM Providers & Configuration Guide

ScopeForge provides a model-agnostic agent harness supporting hosted frontier models, local open-source weights, and aggregator proxies.

---

## 1. Switching Models

You can switch models in **three ways**:

### A. Via TUI Slash Command
Type in the prompt bar:
```text
/model claude-3-7-sonnet
/model gpt-4o
/model ollama-llama3
/model openrouter-claude
/model groq-llama3
/model mock-secops
```

### B. Via Interactive Picker Modal
Type `/model` without arguments (or use the Model Picker shortcut):
- Navigates an interactive table of all registered providers.
- Select with <kbd>↑</kbd> / <kbd>↓</kbd> and press <kbd>Enter</kbd> to activate.

### C. Via Configuration File
Edit `.scopeforge/providers.yaml`:
```yaml
active: claude-3-7-sonnet
providers:
  claude-3-7-sonnet:
    name: claude-3-7-sonnet
    provider: anthropic
    model: claude-3-7-sonnet-20250219
    temperature: 0.1
  gpt-4o:
    name: gpt-4o
    provider: openai
    model: gpt-4o
    temperature: 0.1
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
```bash
export OPENROUTER_API_KEY="sk-or-..."
```
Switch via `/model openrouter-claude`.

### 5. Groq (Ultra-Low Latency Inference)
```bash
export GROQ_API_KEY="gsk_..."
```
Switch via `/model groq-llama3`.

### 6. Custom OpenAI-Compatible Endpoints (vLLM / LMStudio / LocalAI)
Add a custom provider to `.scopeforge/providers.yaml`:
```yaml
providers:
  local-vllm:
    name: local-vllm
    provider: custom
    model: Qwen/Qwen2.5-Coder-32B-Instruct
    api_base: http://localhost:8000/v1
    api_key: none
    temperature: 0.2
```
Then switch via `/model local-vllm`.

### 7. Mock SecOps Evaluator (Zero API Key / Offline Demo)
If no API keys are configured, ScopeForge operates using the built-in `mock-secops` provider. It simulates realistic reconnaissance, SAST code audits, CVE triage, and falsifiable PoCs without making external network calls.

---

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
