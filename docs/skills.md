# Specialized Agent Skills Guide

ScopeForge implements a modular **Skills System** modeled after Claude Code and Open Code. Skills allow you to equip cybersecurity agents with specialized domain knowledge, operational playbooks, and methodologies.

---

## 1. How Skills Work

1. **Storage Location**: Skills are stored in `.scopeforge/skills/<skill-name>/SKILL.md` (or workspace `./skills/<skill-name>/SKILL.md`).
2. **Metadata & Triggers**: Each skill defines YAML frontmatter containing `name`, `description`, `author`, `version`, and `triggers`.
3. **Auto-Detection**: When a user's prompt matches a skill's triggers (e.g., prompt contains "subdomain", "idor", "cve", or "proxy"), ScopeForge automatically loads that skill.
4. **Prompt Augmentation**: The skill's instructions are dynamically injected into the agent's system prompt in LangGraph for that turn.

---

## 2. Directory Layout

```
.scopeforge/skills/
├── subdomain-recon/
│   └── SKILL.md
├── api-idor-audit/
│   └── SKILL.md
├── cve-triage/
│   └── SKILL.md
├── reverse-proxy-bypass/
│   └── SKILL.md
└── my-custom-skill/
    └── SKILL.md
```

---

## 3. Creating a Custom Skill

To create a new skill, create a directory under `.scopeforge/skills/` and add a `SKILL.md` file:

### Example: `jwt-security-audit/SKILL.md`

```markdown
---
name: jwt-security-audit
description: Playbook for testing JSON Web Token (JWT) vulnerabilities and signature bypasses.
triggers: ["jwt", "token", "signature", "none algorithm", "jwks", "bearer"]
author: SecOps Team
version: 1.0.0
---

# JWT Security Testing Methodology

When inspecting authentication mechanisms utilizing JSON Web Tokens:

1. **Algorithm Confusion**:
   - Check if the server accepts `alg: "none"` or lowercase variants (`None`, `NONE`).
   - Test RS256 to HS256 key confusion by signing with the server's public RSA key as the HMAC secret.

2. **Header Parameter Injections**:
   - **`jwk` parameter injection**: Supply an embedded attacker-controlled public key.
   - **`jku` (JWK Set URL)**: Test if the server fetches keys from arbitrary external domains without allowlisting.
   - **`kid` (Key ID) injection**: Check for SQL injection or path traversal (`/dev/null`) inside the `kid` header.

3. **ScopeGate Check**:
   - Never send tokens or exploit payloads against out-of-scope targets.
   - Preserve evidence in `.scopeforge/evidence/`.
```

---

## 4. Managing Skills in the TUI

### List All Skills
Type in prompt:
```text
/skills
# or:
/skill list
```
Displays all discovered skills, descriptions, trigger keywords, and current activation status.

### Manually Toggle a Skill
```text
/skill api-idor-audit
```
Output:
`✓ Activated specialized skill: [api-idor-audit]`

### View Skills in the Sidebar
Press <kbd>F2</kbd> or click the **Skills** tab in the right sidebar to browse all loaded capabilities and their triggers in real-time.
