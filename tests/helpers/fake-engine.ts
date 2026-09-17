// Deterministic fake engine for CLI/TUI tests (no subprocess).
import type { RpcEvent } from "../../packages/protocol-ts/src/client.js";
import type { EngineSession } from "../../apps/terminal/src/commands.js";

function rpcErr(code: number, message: string): Error {
  const e = new Error(`RPC {"code":${code},"message":"${message}"}`) as Error & { code: number };
  e.code = code;
  return e;
}

export interface FakeProvider {
  name: string;
  credential?: string;
  transport?: string;
  type?: string;
}

export class FakeEngine implements EngineSession {
  runs = new Map<string, { run_id: string; mode: string; status: string; events: number }>();
  counter = 0;
  seq = 0;
  notified: { method: string; params: Record<string, unknown> }[] = [];
  extraProviders: FakeProvider[] = [];
  caseData = new Map<string, Record<string, unknown>[]>();
  evidenceData = new Map<string, Record<string, unknown>[]>();
  evCounter = 0;

  private requireRun(runId: string): void {
    if (!this.runs.has(runId)) throw rpcErr(-32602, `unknown run_id ${JSON.stringify(runId)}`);
  }
  private handler: ((e: never) => void) | undefined;

  get onEvent(): ((e: never) => void) | undefined {
    return this.handler;
  }
  set onEvent(f: ((e: never) => void) | undefined) {
    this.handler = f;
  }

  private emit(ev: Partial<RpcEvent> & { method: string }): void {
    this.seq += 1;
    this.handler?.({
      jsonrpc: "2.0",
      method: ev.method,
      params: { seq: this.seq, ...(ev.params as object) },
      protocol_version: "0.1.0",
      trace_id: "fake",
      ts: new Date().toISOString(),
    } as never);
  }

  async request(method: string, params: Record<string, unknown> = {}): Promise<unknown> {
    if (method === "initialize") return { protocol_version: "0.1.0", engine: "fake/0.1.0" };
    if (method === "provider.list") {
      const base = [
        { name: "mock", builtin: true, adapter: "mock", endpoint_class: "local-mock", transport: "local" },
        { name: "deepseek", builtin: true, adapter: "deepseek", endpoint_class: "openai-chat", transport: "hosted" },
      ];
      for (const x of this.extraProviders) {
        base.push({
          name: x.name, builtin: false, type: x.type ?? "deepseek", adapter: "deepseek",
          endpoint_class: "openai-chat", transport: x.transport ?? "hosted",
          credential: x.credential,
        } as never);
      }
      return { providers: base };
    }
    if (method === "model.list") {
      const p = String(params.provider);
      const extra = this.extraProviders.some((x) => x.name === p);
      if (p !== "mock" && p !== "deepseek" && !extra) {
        throw rpcErr(-32602, `unknown provider ${JSON.stringify(p)}`);
      }
      return p === "mock"
        ? { models: ["mock-turn"] }
        : { models: ["deepseek-chat", "deepseek-reasoner"] };
    }
    if (method === "model.probe") {
      const p = String(params.provider);
      const model = String(params.model ?? "default");
      const extra = this.extraProviders.find((x) => x.name === p);
      if (p !== "mock" && p !== "deepseek" && !extra) {
        throw rpcErr(-32602, `unknown provider ${JSON.stringify(p)}`);
      }
      const reasoner = model === "deepseek-reasoner";
      return {
        provider: p, model, adapter: "deepseek", adapter_version: "0.1.0",
        endpoint_class: p === "mock" ? "local-mock" : "openai-chat",
        transport: extra?.transport ?? (p === "mock" ? "local" : "hosted"),
        capabilities: {
          streaming: true, tool_calling: !reasoner, parallel_tools: !reasoner,
          strict_structured_output: true, structured_via_tool: false,
          vision: true, reasoning: reasoner, model_listing: true,
        },
        verified_live: p === "mock" || params.live === true,
        credential: extra?.credential,
        reachability: p === "mock" ? "local (no network)" : undefined,
      };
    }
    if (method === "run.create") {
      this.counter += 1;
      const run_id = `run_${String(this.counter).padStart(4, "0")}`;
      const mode = String(params.mode ?? "plan");
      this.runs.set(run_id, { run_id, mode, status: "draft", events: 0 });
      return { run_id, mode, status: "draft" };
    }
    if (method === "run.list") return { runs: [...this.runs.values()] };
    if (method === "run.status") {
      const r = this.runs.get(String(params.run_id));
      if (!r) throw rpcErr(-32602, `unknown run_id ${JSON.stringify(params.run_id)}`);
      return { ...r, budget: { requests_used: 0, requests_limit: null } };
    }
    if (method === "run.cancel") {
      const r = this.runs.get(String(params.run_id));
      if (!r) throw rpcErr(-32602, "unknown run_id");
      r.status = "paused";
      return { run_id: r.run_id, cancelled: true };
    }
    if (method === "run.close") {
      const r = this.runs.get(String(params.run_id));
      if (!r) throw rpcErr(-32602, "unknown run_id");
      r.status = "closed";
      return { run_id: r.run_id, closed: true };
    }
    if (method === "event.subscribe") {      const count = Math.min(Number(params.count ?? 10), 5000);
      const rid = params.run_id ? String(params.run_id) : undefined;
      for (let i = 0; i < count; i++) {
        this.emit({ method: "token.delta", params: { index: i, total: count, text: "tok", run_id: rid } });
        if (i % 50 === 0) await new Promise((r) => setTimeout(r, 0));
      }
      if (params.demoApproval) {
        this.seq += 1;
        this.handler?.({
          jsonrpc: "2.0", method: "approval.required",
          params: {
            seq: this.seq, tool: "http.request", destination: "https://authorized.example/export",
            method: "GET", requests: 1, sideEffects: "read-only fetch",
            permissionRef: "P-2026-09-15",
          },
          protocol_version: "0.1.0", trace_id: "fake", ts: new Date().toISOString(),
        } as never);
      }
      return { received: count, cancelled: false, last_seq: this.seq };
    }
    if (method === "case.create") {
      const rid = String(params.run_id);
      this.requireRun(rid);
      const c = (params.case ?? {}) as Record<string, unknown>;
      if (!c.check_id || !c.hypothesis) throw rpcErr(-32602, "invalid case: missing check_id/hypothesis");
      const list = this.caseData.get(rid) ?? [];
      const record = { result: "queued", evidence_refs: [], ...c,
        case_id: `C-${String(list.length + 1).padStart(4, "0")}` };
      list.push(record);
      this.caseData.set(rid, list);
      return { case: record };
    }
    if (method === "case.list") {
      const rid = String(params.run_id);
      this.requireRun(rid);
      const list = this.caseData.get(rid) ?? [];
      const result = params.result ? list.filter((c) => c.result === params.result) : list;
      return { cases: result };
    }
    if (method === "case.show") {
      const rid = String(params.run_id);
      this.requireRun(rid);
      const found = (this.caseData.get(rid) ?? []).find((c) => c.case_id === params.case_id);
      if (!found) throw rpcErr(-32602, `unknown case ${JSON.stringify(params.case_id)}`);
      return { case: found };
    }
    if (method === "case.update") {
      const rid = String(params.run_id);
      this.requireRun(rid);
      const list = this.caseData.get(rid) ?? [];
      const found = list.find((c) => c.case_id === params.case_id) as Record<string, unknown> | undefined;
      if (!found) throw rpcErr(-32602, `unknown case ${JSON.stringify(params.case_id)}`);
      const fields = (params.fields ?? {}) as Record<string, unknown>;
      Object.assign(found, fields);
      if (found.result !== "queued" && !(found.evidence_refs as unknown[]).length) {
        throw rpcErr(-32602, "executed results require evidence_refs");
      }
      return { case: found };
    }
    if (method === "artifact.import") {
      const rid = String(params.run_id);
      this.requireRun(rid);
      if (!params.path && !params.content_b64) throw rpcErr(-32602, "artifact.import needs path or content_b64");
      this.evCounter += 1;
      const meta = { evidence_id: `E-${String(this.evCounter).padStart(3, "0")}`,
        sha256: "ab".repeat(32), source_type: params.source_type ?? "file",
        byte_size: 5, redaction_state: "original", description: params.path ?? "inline" };
      const list = this.evidenceData.get(rid) ?? [];
      list.push(meta);
      this.evidenceData.set(rid, list);
      return { evidence_id: meta.evidence_id, sha256: meta.sha256 };
    }
    if (method === "evidence.list") {
      const rid = String(params.run_id);
      this.requireRun(rid);
      return { evidence: this.evidenceData.get(rid) ?? [] };
    }
    if (method === "evidence.show") {
      const rid = String(params.run_id);
      this.requireRun(rid);
      const found = (this.evidenceData.get(rid) ?? []).find((m) => m.evidence_id === params.evidence_id);
      if (!found) throw rpcErr(-32602, `unknown evidence ${JSON.stringify(params.evidence_id)}`);
      return params.preview ? { evidence: found, preview: "preview-bytes" } : { evidence: found };
    }
    if (method === "evidence.redact") {
      const rid = String(params.run_id);
      this.requireRun(rid);
      const found = (this.evidenceData.get(rid) ?? []).find((m) => m.evidence_id === params.evidence_id);
      if (!found) throw rpcErr(-32602, `unknown evidence ${JSON.stringify(params.evidence_id)}`);
      this.evCounter += 1;
      const derived = { ...found, evidence_id: `E-${String(this.evCounter).padStart(3, "0")}`,
        redaction_state: "redacted", derived_from: found.evidence_id };
      (this.evidenceData.get(rid) ?? []).push(derived);
      return { derived };
    }
    if (method === "coverage.get") {
      this.requireRun(String(params.run_id));
      const ids = ["T01","T02","T03","T04","T05","T06","T07","T08","T09","T10","T11","T12"];
      return { coverage: { categories: ids.map((id) => ({ id, applicability: "not_assessed", checks: [] })) } };
    }
    if (method === "coverage.set") {
      this.requireRun(String(params.run_id));
      return { coverage: { categories: params.categories } };
    }
    if (method === "report.build") {
      const rid = String(params.run_id);
      this.requireRun(rid);
      const f = (params.finding ?? {}) as Record<string, unknown>;
      if (!f.title || !f.evidence) throw rpcErr(-32602, "incomplete draft, missing: title/evidence");
      return { finding_id: "F-0001", path: `/fake/${rid}/findings/F-0001/report.md` };
    }
    if (method === "export.create") {
      this.requireRun(String(params.run_id));
      return { path: `/fake/${params.run_id}/export.tar.gz`, sha256: "cd".repeat(32),
        files: 4, warnings: [] };
    }
    if (method === "run.rebuild") {
      return { counts: { runs: this.runs.size, cases: 0, evidence: 0 } };
    }
    if (method === "tool.preview") {
      return { allowed: false, reasons: ["fake preview"], approval_required: true,
        approval_preview: { scope: "http.request:GET:fake", destination: "fake" } };
    }
    if (method === "tool.execute") {
      const action = (params.action ?? {}) as Record<string, unknown>;
      return { sent: true, allowed: true, reasons: ["fake"], status: 200,
        headers: {}, body_preview: "{}", evidence_ids: ["E-001"], requests_made: 1,
        action_echo: action.method ?? "GET" };
    }
    if (method === "approval.respond") {
      return { recorded: true, approved: params.approved !== false };
    }
    if (method === "lab.list") {
      return { labs: [{ name: "tenancy-demo", objective: "fake" }] };
    }
    if (method === "lab.launch") {
      if (params.lab !== "tenancy-demo") throw rpcErr(-32602, `unknown lab ${JSON.stringify(params.lab)}`);
      return { name: "tenancy-demo", url: "http://127.0.0.1:9", manifest: { budgets: { requests_total: 50 } } };
    }
    if (method === "lab.stop" || method === "lab.reset") {
      return method === "lab.stop" ? { stopped: true } : { reset: true };
    }
    if (method === "experiment.compare") {
      const base = (params.baseline ?? {}) as Record<string, unknown>;
      const variant = (params.variant ?? {}) as Record<string, unknown>;
      const keys = ["method", "url", "actor_ref", "body", "headers"];
      const differing = keys.filter((k) => (base[k] ?? "") !== (variant[k] ?? ""));
      if (differing.length !== 1) throw rpcErr(-32602, `experiment requires exactly one changed variable, got ${differing.length}`);
      return { changed_variable: differing[0],
        baseline: { status: 200, evidence_ids: ["E-001"] },
        variant: { status: 403, evidence_ids: ["E-002"] },
        diff: { kind: "text", lines: ["-a", "+b"] } };
    }
    if (method === "evidence.diff") {
      return { baseline_id: params.baseline_id, variant_id: params.variant_id,
        kind: "text", lines: ["-a", "+b"] };
    }
    if (method === "chat.submit") {
      const rid = String(params.run_id);
      this.requireRun(rid);
      return { role: "research_assistant", model: "mock-turn",
        output: { next_action: "inspect supplied artifacts", tool: "artifact.read",
          bounded_args: {}, evidence_needed: ["baseline"] },
        gaps: [], structured_via: "native",
        decision: { allowed: false, reasons: ["mode=plan: target contact disabled"], approval_required: false },
        stored: true };
    }
    if (method === "case.execute") {
      const rid = String(params.run_id);
      this.requireRun(rid);
      return { proposal: { next_action: "diff baseline vs variant" }, gaps: [],
        decision: { allowed: true, reasons: ["local action admitted"] } };
    }
    if (method === "coach.score") {
      const rid = String(params.run_id);
      this.requireRun(rid);
      const skills = ["scope_discipline","product_modeling","hypothesis_quality","experimental_control",
        "impact_restraint","evidence_quality","alternative_explanations","reporting","retesting","tool_literacy"];
      return { case_id: String(params.case_id), scores: Object.fromEntries(skills.map((s) => [s, { score: 2, reasons: ["fake"] }])),
        questions: ["What rule did you expect?"] };
    }
    if (method === "coverage.report") {
      const rid = String(params.run_id);
      this.requireRun(rid);
      const ids = ["T01","T02","T03","T04","T05","T06","T07","T08","T09","T10","T11","T12"];
      return { run_id: rid, categories: ids.map((id) => ({ id, checks: [{ check_id: `${id}.1`, status: "not_executed", cases: [] }] })) };
    }
    if (method === "policy.import") {
      const file = String(params.file ?? "");
      const inline = params.policy as Record<string, unknown> | undefined;
      if (inline && typeof inline === "object") {
        return { policy: { program_name: (inline.program_name as string) ?? "Fake lab",
          mode: (inline.mode as string) ?? "live",
          include: ((inline.include as unknown[]) ?? []).length,
          exclude: ((inline.exclude as unknown[]) ?? []).length, unresolved: [] } };
      }
      if (!file || file.startsWith("http")) {
        throw rpcErr(-32602, file.startsWith("http") ? "URL policy import needs Phase-7 transport" : "policy file not found");
      }
      return { policy: { program_name: "Fake program", mode: "plan", include: 0, exclude: 0,
        unresolved: ["no included assets: scope is UNKNOWN (never unrestricted)"] } };
    }
    if (method === "policy.check") {
      const action = (params.action ?? {}) as Record<string, unknown>;
      const live = action.kind === "http.request" || action.kind === "browser.navigate" || action.kind === "tool.exec";
      if (live) return { allowed: false, reasons: ["mode=plan: target contact disabled (zero egress)"], approval_required: false };
      return { allowed: true, reasons: ["local action admitted"], approval_required: false };
    }
    if (method === "policy.status") {
      const rid = String(params.run_id);
      this.requireRun(rid);
      return { run_id: rid, mode: "plan", policy_source: "", unresolved: ["scope unknown"],
        budgets: { limits: { requests_total: 0 }, used: { requests: 0, bytes: 0, cost: 0 } } };
    }
    if (method === "scope.show") {
      const rid = String(params.run_id);
      this.requireRun(rid);
      return { run_id: rid, program_name: "Fake program", mode: "plan", policy_source: "",
        included_assets: [], exclusions: [], methods_allow: ["GET", "HEAD"], methods_deny: [],
        unresolved: ["scope unknown"], budgets: { requests_total: 0 } };
    }
    if (method === "tool.list") {
      return { tools: [{ name: "nuclei", risk_class: "R2", executable: "nuclei", allowed_args: ["-target"] }] };
    }
    if (method === "tool.invoke") {
      const exe = String(params.executable ?? "");
      if (exe === "/no/such/bin") throw rpcErr(-32000, "tool executable not found: /no/such/bin");
      if (params.target_url && String(params.target_url).includes("evil.example")) {
        throw rpcErr(-32602, "tool egress denied: not in included scope");
      }
      return { tool: String(params.name ?? "tool"), exit_code: 0, stdout: "ok", stderr: "", truncated: false, parsed: null };
    }
    if (method === "mcp.add") {
      const name = String(params.name ?? "");
      if (!name) throw rpcErr(-32602, "mcp add needs name");
      return { name, enabled: false, command: String(params.command ?? "") };
    }
    if (method === "mcp.list") {
      return { servers: [] };
    }
    if (method === "mcp.enable" || method === "mcp.disable" || method === "mcp.remove") {
      const name = String(params.name ?? "");
      if (!name) throw rpcErr(-32602, `mcp ${method.split(".")[1]} needs name`);
      return method === "mcp.remove" ? { removed: true } : { name, enabled: method === "mcp.enable" };
    }
    if (method === "har.import" || method === "burp.import") {
      const rid = String(params.run_id);
      this.requireRun(rid);
      this.evCounter += 1;
      const eid = `E-${String(this.evCounter).padStart(3, "0")}`;
      const meta = { evidence_id: eid, sha256: "ab".repeat(32), source_type: method.split(".")[0], byte_size: 5 };
      const list = this.evidenceData.get(rid) ?? [];
      list.push(meta);
      this.evidenceData.set(rid, list);
      return { imported: 1, evidence_ids: [eid], filename: String(params.path ?? "file") };
    }
    throw rpcErr(-32601, `Method not found: ${method}`);
  }

  notify(method: string, params: Record<string, unknown> = {}): void {
    this.notified.push({ method, params });
  }

  async stop(): Promise<void> {
    /* nothing */
  }
}
