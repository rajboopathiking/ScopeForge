// Display redaction (defense in depth). Secrets must never reach model context
// (engine-side, Phase 3/4) nor the screen. This module guards the TUI/CLI
// rendering path: every user-visible string passes through redactText().
export const REDACTED = "[redacted]";

interface Rule {
  name: string;
  re: RegExp;
}

const RULES: Rule[] = [
  { name: "aws-access-key", re: /\bAKIA[0-9A-Z]{16}\b/g },
  {
    name: "aws-secret",
    re: /\baws_secret_access_key\s*[:=]\s*['"]?[A-Za-z0-9/+=]{30,}['"]?/gi,
  },
  { name: "github-token", re: /\b(ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9]{20,}\b/g },
  { name: "github-pat", re: /\bgithub_pat_[A-Za-z0-9_]{20,}\b/g },
  { name: "slack-token", re: /\bxox[baprs]-[A-Za-z0-9-]{10,}\b/g },
  { name: "openai-key", re: /\bsk-(proj-)?[A-Za-z0-9_-]{20,}\b/g },
  { name: "anthropic-key", re: /\bsk-ant-[A-Za-z0-9_-]{20,}\b/g },
  { name: "bearer", re: /\bBearer\s+[A-Za-z0-9\-._~+/=]{12,}\b/gi },
  {
    name: "basic-auth",
    re: /\bBasic\s+[A-Za-z0-9+/=]{12,}\b/gi,
  },
  {
    name: "private-key",
    re: /-----BEGIN (?:RSA |EC |OPENSSH |DSA )?PRIVATE KEY-----[\s\S]*?-----END (?:RSA |EC |OPENSSH |DSA )?PRIVATE KEY-----/g,
  },
  {
    name: "credential-assignment",
    // password = "..." / api_key: '...' / token=... (quoted or long bare values)
    re: /\b(password|passwd|pwd|secret|api[_-]?key|auth[_-]?token|access[_-]?token|session[_-]?id|cookie)\s*[:=]\s*("[^"]+"|'[^']+'|[^\s,;}]{8,})/gi,
  },
  {
    name: "signed-url-sig",
    re: /([?&](?:signature|sig|token|key)=)[^&\s"']+/gi,
  },
];

export interface RedactResult {
  text: string;
  redactions: number;
  rules: string[];
}

/** Redact secret shapes from display text. Never throws; returns original on error. */
export function redactText(input: string): RedactResult {
  try {
    let text = input;
    let redactions = 0;
    const rules: string[] = [];
    for (const rule of RULES) {
      rule.re.lastIndex = 0;
      if (rule.re.test(text)) {
        rule.re.lastIndex = 0;
        text = text.replace(rule.re, (m) => {
          // Preserve the `key=` prefix for assignment/sig rules so the label survives.
          if (rule.name === "credential-assignment" || rule.name === "signed-url-sig") {
            return m.replace(/[:=]\s*["']?[^"']*["']?$/, (tail) => {
              const eq = tail.match(/[:=]/)?.[0] ?? "=";
              const quote = tail.trimEnd().endsWith('"') ? '"' : tail.trimEnd().endsWith("'") ? "'" : "";
              return `${eq} ${quote}${REDACTED}${quote}`;
            });
          }
          return REDACTED;
        });
        redactions++;
        rules.push(rule.name);
      }
    }
    return { text, redactions, rules };
  } catch {
    return { text: input, redactions: 0, rules: [] };
  }
}

/** True if any secret shape is present (used by tests + leak guards).
 * Idempotent: already-redacted markers never count as secrets. */
export function containsSecret(input: string): boolean {
  try {
    const stripped = input.replace(/\[redacted[^\]]*\]/g, "");
    return RULES.some((r) => {
      r.re.lastIndex = 0;
      return r.re.test(stripped);
    });
  } catch {
    return false;
  }
}

/** Deep-redact strings inside an unknown JSON value (events, tool output preview). */
export function redactValue<T>(value: T): { value: T; redactions: number } {
  let redactions = 0;
  const walk = (v: unknown): unknown => {
    if (typeof v === "string") {
      const r = redactText(v);
      redactions += r.redactions;
      return r.text;
    }
    if (Array.isArray(v)) return v.map(walk);
    if (v && typeof v === "object") {
      const out: Record<string, unknown> = {};
      for (const [k, val] of Object.entries(v as Record<string, unknown>)) {
        // Never render secret-named keys' values, even if the shape is novel.
        if (/^(authorization|cookie|set-cookie|api[_-]?key|token|secret|password)$/i.test(k)) {
          redactions++;
          out[k] = REDACTED;
        } else {
          out[k] = walk(val);
        }
      }
      return out;
    }
    return v;
  };
  return { value: walk(value) as T, redactions };
}
