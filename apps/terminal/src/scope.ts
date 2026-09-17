// Scope validation stub (Phase 2): canonicalize + explain. The deterministic
// policy kernel (structured rules, DNS/IP, redirects, budgets) lands in Phase 5.
// This module never authorizes anything: verdict is always "unknown" without a
// loaded policy, and unknown means NOT authorized.
export interface ScopeVerdict {
  input: string;
  canonical: string | null;
  verdict: "unknown";
  authorized: false;
  reasons: string[];
}

export function canonicalizeTarget(input: string): { canonical: string | null; notes: string[] } {
  const notes: string[] = [];
  let text = input.trim();
  if (!/^[a-zA-Z][a-zA-Z0-9+.-]*:\/\//.test(text)) {
    text = `https://${text}`;
    notes.push("assumed https scheme (no scheme supplied)");
  }
  try {
    const u = new URL(text);
    let host = u.hostname.toLowerCase();
    if (host.endsWith(".")) {
      host = host.slice(0, -1);
      notes.push("stripped trailing dot");
    }
    const defPort = (u.protocol === "https:" && u.port === "443") || (u.protocol === "http:" && u.port === "80");
    const port = defPort || !u.port ? "" : `:${u.port}`;
    if (defPort) notes.push("removed default port");
    const path = u.pathname === "/" ? "" : u.pathname;
    return { canonical: `${u.protocol}//${host}${port}${path}`, notes };
  } catch {
    return { canonical: null, notes: ["not a parseable URL/host"] };
  }
}

export function validateScope(input: string): ScopeVerdict {
  const { canonical, notes } = canonicalizeTarget(input);
  const reasons = [...notes];
  if (canonical === null) {
    reasons.push("target is not parseable; cannot be in scope");
  } else {
    reasons.push("no policy loaded in Phase 2; scope is unknown, never unrestricted");
    reasons.push("live contact requires `run start --mode live` review (Phase 5 gate)");
  }
  return { input, canonical, verdict: "unknown", authorized: false, reasons };
}
