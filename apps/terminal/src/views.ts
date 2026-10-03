// Pure view renderers: `render(state) -> lines`. Renderer-agnostic (ADR-04):
// the plain backend prints these lines; a future OpenTUI binding maps them to
// widgets. All views are snapshot/contract-tested here; secrets are redacted
// at this boundary as defense in depth.
import { redactText } from "@scopeforge/protocol";
import type { RpcEvent } from "@scopeforge/protocol";
import type { CaseRecord } from "./case-store.js";
import { fit, paint, sym, type Theme } from "./themes.js";

export interface RunSummary {
  run_id: string;
  mode: string;
  status: string;
  updated_at?: string;
  events?: number;
  last_seq?: number;
}

export interface StatusInfo {
  mode: string;
  runId: string | null;
  model: string;
  budgetUsed: number;
  budgetLimit: number | null;
  egress: "none" | "policy-gated";
}

const MODE_BADGE: Record<string, string> = {
  plan: "PLAN · no target egress",
  artifacts: "ARTIFACTS · no target egress",
  live: "LIVE · policy-gated egress",
};

export function renderStatusLine(s: StatusInfo, width: number, theme: Theme): string {
  const badge = MODE_BADGE[s.mode] ?? s.mode.toUpperCase();
  const budget =
    s.budgetLimit === null ? `${s.budgetUsed}/unknown` : `${s.budgetUsed}/${s.budgetLimit}`;
  const line = `Status: ${badge} ${sym(theme, "dot")} run ${s.runId ?? "-"} ${sym(theme, "dot")} model ${s.model} ${sym(theme, "dot")} budget ${budget}`;
  return fit(paint(theme, "dim", line), width);
}

export function renderHeader(title: string, width: number, theme: Theme): string {
  return fit(paint(theme, "bold", ` ScopeForge ${sym(theme, "dot")} ${title}`), width);
}

function statusIcon(theme: Theme, status: string): string {
  if (status === "closed") return sym(theme, "closed");
  if (status === "paused") return sym(theme, "paused");
  if (status === "draft") return sym(theme, "draft");
  return sym(theme, "run");
}

export function renderRunSwitcher(runs: RunSummary[], selected: number, theme: Theme): string[] {
  const lines = [paint(theme, "bold", "Runs (recent, resumable, blocked):")];
  if (runs.length === 0) return [...lines, "  (no runs yet — `run start --mode plan`) "];
  runs.forEach((r, i) => {
    const pick = i === selected ? sym(theme, "pick") : " ";
    lines.push(
      `${pick} ${statusIcon(theme, r.status)} ${r.run_id} [${r.mode}] ${r.status} ev=${r.events ?? 0}`,
    );
  });
  return lines;
}

const RESULT_LABEL: Record<string, string> = {
  queued: "QUEUED",
  tested_no_issue_observed: "NO-ISSUE",
  lead: "LEAD (not a finding)",
  validated_finding: "FINDING",
  false_positive: "FALSE-POS",
  blocked: "BLOCKED",
  inconclusive: "INCONCLUSIVE",
};

export function renderQueue(cases: CaseRecord[], selected: number, theme: Theme): string[] {
  const lines = [paint(theme, "bold", "Case queue:")];
  if (cases.length === 0) return [...lines, "  (empty — `case new --check T02.1 ...`) "];
  cases.forEach((c, i) => {
    const pick = i === selected ? sym(theme, "pick") : " ";
    lines.push(`${pick} ${c.case_id} ${c.check_id} ${RESULT_LABEL[c.result] ?? c.result} ${c.hypothesis.slice(0, 60)}`);
  });
  return lines;
}

export function renderConversation(events: RpcEvent[], theme: Theme, limit = 30): string[] {
  const lines = [paint(theme, "bold", "Conversation / experiment:")];
  const tail = events.slice(-limit);
  if (tail.length === 0) return [...lines, "  (no events — journal replays here after restart) "];
  for (const ev of tail) {
    const method = String((ev as { method?: string }).method ?? "event");
    const p = (ev.params ?? {}) as Record<string, unknown>;
    if (method === "token.delta" && typeof p.text === "string") {
      lines.push(`  model: ${redactText(p.text).text}`);
    } else if (method === "agent.turn") {
      lines.push(`  assistant [${String(p.role ?? "agent")}]: ${redactText(String(p.text ?? "")).text}`);
    } else if (method === "chat.local") {
      lines.push(`  you: ${redactText(String(p.text ?? "")).text}`);
    } else if (method === "approval.required") {
      lines.push(`  ${paint(theme, "yellow", "approval required")} — see approval pane`);
    } else if (method === "warning" || method === "error") {
      lines.push(`  ${sym(theme, "warn")} ${redactText(String(p.message ?? method)).text}`);
    } else {
      lines.push(`  ${sym(theme, "dot")} ${method}`);
    }
  }
  return lines;
}

export interface ApprovalProposal {
  tool: string;
  destination: string;
  method: string;
  requests: number;
  sideEffects: string;
  permissionRef: string;
  scope?: string;
}

export function renderApproval(a: ApprovalProposal, width: number, theme: Theme): string[] {
  return [
    paint(theme, "bold", "Approval required (one-shot; --yes never bypasses this gate):"),
    `  tool        : ${a.tool}`,
    `  destination : ${redactText(a.destination).text}`,
    `  method      : ${a.method}`,
    `  requests    : ${a.requests}`,
    `  side effects: ${redactText(a.sideEffects).text}`,
    `  permission  : ${a.permissionRef}`,
    ...(a.scope ? [`  scope       : ${a.scope}`] : []),
    `  ${paint(theme, "green", "[a]pprove")}  ${paint(theme, "red", "[d]eny")}`,
    fit("", width),
  ];
}

export function renderExecution(
  result: { sent?: boolean; allowed?: boolean; status?: number; reasons?: string[];
            approval_required?: boolean; evidence_ids?: string[]; transport_error?: string },
  theme: Theme,
): string[] {
  const lines = [paint(theme, "bold", "Execution result:")];
  if (result.transport_error) {
    lines.push(`  transport failed safely (no partial state): ${result.transport_error}`);
    return lines;
  }
  if (!result.sent) {
    lines.push(`  not sent: ${(result.reasons ?? []).join("; ")}`);
    if (result.approval_required) lines.push("  approve via the modal to proceed (one-shot)");
    return lines;
  }
  lines.push(`  status: ${result.status} evidence: ${(result.evidence_ids ?? []).join(", ")}`);
  return lines;
}

export function renderElicitation(question: string, options: string[], theme: Theme): string[] {
  return [
    paint(theme, "bold", "Input needed:"),
    `  ${redactText(question).text}`,
    ...options.map((o, i) => `  ${i + 1}. ${redactText(o).text}`),
  ];
}

export function renderEvidence(items: { id: string; desc: string }[], theme: Theme): string[] {
  const lines = [paint(theme, "bold", "Evidence (immutable originals; redacted copies for reports):")];
  if (items.length === 0) return [...lines, "  (none yet) "];
  for (const it of items) lines.push(`  ${it.id} ${redactText(it.desc).text}`);
  return lines;
}

/** Case-insensitive subsequence fuzzy match for the command palette. */
export function fuzzyMatch(query: string, target: string): boolean {
  const q = query.toLowerCase();
  const t = target.toLowerCase();
  let i = 0;
  for (const ch of t) {
    if (ch === q[i]) i++;
    if (i === q.length) return true;
  }
  return q.length === 0;
}

export function renderPalette(items: string[], query: string, theme: Theme): string[] {
  const hits = items.filter((it) => fuzzyMatch(query, it)).slice(0, 10);
  const lines = [paint(theme, "bold", `Command palette (/${query}):`)];
  if (hits.length === 0) lines.push("  (no match)");
  hits.forEach((h, i) => lines.push(`  ${i === 0 ? sym(theme, "pick") : " "} ${h}`));
  return lines;
}

export function renderComposer(draft: string, theme: Theme): string[] {
  return [`${paint(theme, "cyan", "scopeforge>")} ${redactText(draft).text}▌`];
}

export function renderCoach(
  caseId: string, scores: Record<string, { score: number; reasons?: string[] }>, theme: Theme,
): string[] {
  const lines = [paint(theme, "bold", `Coach: ${caseId} (rubric 0–4, evidence-cited):`)];
  for (const [skill, rec] of Object.entries(scores)) {
    const reason = (rec.reasons ?? [])[0] ?? "";
    lines.push(`  ${skill}: ${rec.score}/4 — ${redactText(reason).text}`);
  }
  return lines;
}

export function renderCoverageSummary(
  report: { categories: { id: string; checks: { status: string }[] }[] }, theme: Theme,
): string[] {
  const lines = [paint(theme, "bold", "Coverage (executed cases only — catalog is not coverage):")];
  for (const cat of report.categories) {
    const counts: Record<string, number> = {};
    for (const c of cat.checks) counts[c.status] = (counts[c.status] ?? 0) + 1;
    const parts = Object.entries(counts).map(([k, v]) => `${v} ${k}`);
    lines.push(`  ${cat.id}: ${parts.join(", ") || "no checks"}`);
  }
  return lines;
}

export interface ScopeSummary {
  program_name?: string;
  mode?: string;
  policy_source?: string;
  included_assets?: string[];
  exclusions?: string[];
  methods_allow?: string[];
  methods_deny?: string[];
  unresolved?: string[];
  budgets?: Record<string, unknown>;
}

export function renderScope(s: ScopeSummary, theme: Theme): string[] {
  const lines = [paint(theme, "bold", "Authorization & scope (permission is data):")];
  lines.push(`  program: ${s.program_name ?? "(unnamed)"}`);
  lines.push(`  mode: ${String(s.mode ?? "plan").toUpperCase()} ${s.mode === "live" ? "(policy-gated egress)" : "(no target egress)"}`);
  lines.push(`  policy: ${s.policy_source || "(none recorded — live use blocked)"}`);
  const inc = s.included_assets ?? [];
  lines.push(`  included (${inc.length}): ${inc.join(", ") || "(UNKNOWN — never unrestricted)"}`);
  const exc = s.exclusions ?? [];
  if (exc.length > 0) lines.push(`  excluded: ${exc.join(", ")}`);
  lines.push(`  methods: ${(s.methods_allow ?? []).join(",") || "-"} deny: ${(s.methods_deny ?? []).join(",") || "-"}`);
  const unres = s.unresolved ?? [];
  if (unres.length > 0) lines.push(...["  unresolved:", ...unres.map((u) => `    - ${u}`)]);
  if (s.budgets && Object.keys(s.budgets).length > 0) {
    lines.push(`  budgets: ${Object.entries(s.budgets).map(([k, v]) => `${k}=${v ?? "unknown"}`).join(" ")}`);
  }
  return lines;
}

export interface InspectorEntry {
  provider: string;
  type?: string;
  transport?: string;
  model?: string;
  verified?: boolean;
  credential?: string;
  capabilities?: Record<string, unknown>;
  reachability?: string;
}

const CAP_LABELS: [string, string][] = [
  ["streaming", "stream"], ["tool_calling", "tools"], ["parallel_tools", "parallel"],
  ["strict_structured_output", "strict-json"], ["structured_via_tool", "strict-via-tool"],
  ["vision", "vision"], ["reasoning", "reasoning"], ["model_listing", "listing"],
];

export function renderModelInspector(entries: InspectorEntry[], theme: Theme): string[] {
  const lines = [paint(theme, "bold", "Model inspector (declared caps; verified only via live probe):")];
  if (entries.length === 0) return [...lines, "  (no providers — `provider add`) "];
  for (const e of entries) {
    const caps = e.capabilities ?? {};
    const on = CAP_LABELS.filter(([k]) => caps[k] === true).map(([, l]) => l);
    const off = CAP_LABELS.filter(([k]) => caps[k] === false).map(([, l]) => `no-${l}`);
    lines.push(`  ${e.provider}${e.type ? ` [${e.type}]` : ""} ${e.transport ?? ""}`.trimEnd());
    lines.push(`    model: ${e.model ?? "-"}${e.verified ? " (verified live)" : " (declared only)"}`);
    lines.push(`    caps: ${[...on, ...off].join(" ") || "-"}`);
    if (e.credential) lines.push(`    credential: ${e.credential}`);
    if (e.reachability) lines.push(`    reachability: ${redactText(e.reachability).text}`);
  }
  return lines;
}

export function renderHelp(hints: string[], theme: Theme): string[] {
  return [paint(theme, "bold", "Keys:"), ...hints.map((h) => `  ${h}`)];
}

/** Two-column frame with header + status bar; compact under 80 cols (small-terminal fallback). */
export function frame(
  title: string,
  left: string[],
  right: string[],
  status: StatusInfo,
  width: number,
  theme: Theme,
): string[] {
  const out = [renderHeader(title, width, theme)];
  if (width < 80) {
    out.push(paint(theme, "yellow", "(compact layout: narrow terminal)"));
    out.push(...left.slice(0, 12), ...right.slice(0, 12));
  } else {
    const lw = Math.floor((width - 3) / 2);
    const rows = Math.max(left.length, right.length);
    for (let i = 0; i < rows; i++) {
      out.push(`${fit(left[i] ?? "", lw)} | ${(right[i] ?? "").slice(0, width - lw - 3)}`);
    }
  }
  out.push(renderStatusLine(status, width, theme));
  return out;
}
