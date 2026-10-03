// Case store: client-side cases.jsonl (Phase-4-compatible shape from
// schemas/case.schema.json). Typed agent I/O + proof gate land in Phase 6;
// this store gives the Phase-2 queue view and CLI something real to show.
import { existsSync, mkdirSync, readFileSync, writeFileSync, appendFileSync } from "node:fs";
import { join } from "node:path";
import { runDir } from "./home.js";

export type CaseResult =
  | "queued"
  | "tested_no_issue_observed"
  | "lead"
  | "validated_finding"
  | "false_positive"
  | "blocked"
  | "inconclusive";

export const CASE_RESULTS: readonly CaseResult[] = [
  "queued",
  "tested_no_issue_observed",
  "lead",
  "validated_finding",
  "false_positive",
  "blocked",
  "inconclusive",
];

export interface CaseRecord {
  case_id: string;
  check_id: string;
  asset: string;
  feature?: string;
  actor_ref?: string;
  role?: string;
  tenant_ref?: string;
  object_ref?: string;
  workflow_state?: string;
  hypothesis: string;
  expected_rule: string;
  baseline: string;
  changed_variable: string;
  observable_result: string;
  stopping_point: string;
  permission_ref: string;
  result: CaseResult;
  evidence_refs?: string[];
  observed_behavior?: string | null;
  inferred_impact?: string | null;
  next_action?: string;
}

export function casesFile(home: string, runId: string): string {
  return join(runDir(home, runId), "cases.jsonl");
}

/** Validate the required one-variable shape. Returns human-readable errors. */
export function validateCase(c: Partial<CaseRecord>): string[] {
  const errs: string[] = [];
  const need = (k: keyof CaseRecord, label: string) => {
    if (!c[k] || String(c[k]).trim().length === 0) errs.push(`missing ${label}`);
  };
  if (c.case_id && !/^C-\d{4}$/.test(c.case_id)) errs.push("case_id must match C-0000");
  if (c.check_id && !/^T(0[1-9]|1[0-2])\.\d+$/.test(c.check_id))
    errs.push("check_id must match T01.1–T12.n");
  need("check_id", "check_id (e.g. T02.1)");
  need("asset", "asset");
  need("hypothesis", "hypothesis");
  need("expected_rule", "expected_rule");
  need("baseline", "baseline");
  need("changed_variable", "changed_variable (exactly one)");
  need("observable_result", "observable_result");
  need("stopping_point", "stopping_point");
  need("permission_ref", "permission_ref");
  if (c.result && !(CASE_RESULTS as readonly string[]).includes(c.result))
    errs.push(`result must be one of ${CASE_RESULTS.join(", ")}`);
  return errs;
}

export function nextCaseId(home: string, runId: string): string {
  let max = 0;
  for (const c of listCases(home, runId)) {
    const m = /^C-(\d{4})$/.exec(c.case_id);
    if (m) max = Math.max(max, Number(m[1]));
  }
  return `C-${String(max + 1).padStart(4, "0")}`;
}

export function appendCase(
  home: string,
  runId: string,
  data: Omit<CaseRecord, "case_id" | "result"> & { result?: CaseResult },
): { record?: CaseRecord; errors?: string[] } {
  const record = {
    result: "queued" as CaseResult,
    evidence_refs: [],
    ...data,
    case_id: nextCaseId(home, runId),
  } as CaseRecord;
  const errors = validateCase(record);
  if (errors.length > 0) return { errors };
  mkdirSync(runDir(home, runId), { recursive: true });
  appendFileSync(casesFile(home, runId), JSON.stringify(record) + "\n");
  return { record };
}

export function listCases(home: string, runId: string, result?: string): CaseRecord[] {
  const f = casesFile(home, runId);
  if (!existsSync(f)) return [];
  const out: CaseRecord[] = [];
  for (const line of readFileSync(f, "utf8").split("\n")) {
    if (!line.trim()) continue;
    try {
      const c = JSON.parse(line) as CaseRecord;
      if (!result || c.result === result) out.push(c);
    } catch {
      /* skip corrupt lines; Phase 4 adds checksums */
    }
  }
  return out;
}

export function showCase(home: string, runId: string, caseId: string): CaseRecord | undefined {
  return listCases(home, runId).find((c) => c.case_id === caseId);
}

/** Rewrite one case's result (append-only log is Phase 4; v0 rewrites atomically). */
export function setCaseResult(
  home: string,
  runId: string,
  caseId: string,
  result: CaseResult,
  observed?: string,
): boolean {
  const f = casesFile(home, runId);
  if (!existsSync(f)) return false;
  let changed = false;
  const lines = readFileSync(f, "utf8").split("\n");
  const next = lines.map((line) => {
    if (!line.trim()) return line;
    try {
      const c = JSON.parse(line) as CaseRecord;
      if (c.case_id === caseId) {
        changed = true;
        return JSON.stringify({ ...c, result, observed_behavior: observed ?? c.observed_behavior });
      }
    } catch {
      /* keep */
    }
    return line;
  });
  if (changed) {
    const tmp = `${f}.tmp`;
    writeFileSync(tmp, next.join("\n"));
    writeFileSync(f, readFileSync(tmp, "utf8"));
  }
  return changed;
}
