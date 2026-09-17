// Plain TUI backend (ADR-04: OpenTUI is NO-GO for now). Keyboard-first,
// screen-reader-friendly `--plain` mode, small-terminal compact layout.
// Views stay pure (`views.ts`); this module owns state, keys, and the engine
// subscription. Crash-resume: journal replay + re-subscribe from maxSeq.
import { createInterface } from "node:readline";
import type { RpcEvent } from "@scopeforge/protocol";
import { listCases, type CaseRecord } from "./case-store.js";
import type { EngineSession } from "./commands.js";
import { ensureHome } from "./home.js";
import { journalAppend, journalReplay } from "./journal.js";
import { actionFor, keyName, loadKeymap, type Action, type Keymap } from "./keys.js";
import { resolveTheme, paint, type Theme } from "./themes.js";
import {
  frame,
  renderApproval,
  renderCoach,
  renderComposer,
  renderConversation,
  renderCoverageSummary,
  renderElicitation,
  renderEvidence,
  renderExecution,
  renderHelp,
  renderModelInspector,
  renderPalette,
  renderQueue,
  renderRunSwitcher,
  renderScope,
  type ApprovalProposal,
  type InspectorEntry,
  type RunSummary,
  type StatusInfo,
} from "./views.js";

export interface TuiOptions {
  home: string;
  runId?: string;
  plain: boolean;
  once: boolean;
  width: number;
  height?: number;
  theme: Theme;
  out: (s: string) => void;
  err: (s: string) => void;
  stdin: NodeJS.ReadStream;
  isTTY: boolean;
  streamCount?: number;
  engine: (home: string) => Promise<EngineSession>;
}

export type Pane = "runs" | "queue" | "chat";

export interface TuiState {
  home: string;
  runs: RunSummary[];
  selRun: number;
  runId: string | null;
  mode: string;
  cases: CaseRecord[];
  selCase: number;
  journaled: RpcEvent[];
  live: RpcEvent[];
  generation: number;
  pane: Pane;
  palette: string | null;
  overlay: { title: string; lines: string[] } | null;
  modal: ApprovalProposal | null;
  elicitation: { question: string; options: string[] } | null;
  composer: string;
  help: boolean;
  status: StatusInfo;
}

export const PALETTE_ITEMS = [
  "run list", "run status", "run close", "case new", "case list", "case execute",
  "scope show", "scope validate", "model inspect", "evidence list", "evidence diff",
  "coach", "coverage", "doctor", "help", "quit",
];

/** One-line display summary of a role turn (details live in the journal). */
export function summarizeTurn(result: {
  role?: string; output?: unknown; gaps?: string[];
}): string {
  const gaps = result.gaps ?? [];
  const out = result.output as Record<string, unknown> | null;
  if (!out) return `no proposal (${gaps.join("; ") || "empty output"})`;
  if (typeof out.verdict === "string") return `verdict: ${out.verdict}`;
  if (typeof out.check_id === "string" && typeof out.hypothesis === "string") {
    return `${out.check_id}: ${String(out.hypothesis).slice(0, 120)}`;
  }
  if (typeof out.next_action === "string") return String(out.next_action).slice(0, 160);
  return JSON.stringify(out).slice(0, 160);
}

export function createState(home: string, model = "unconfigured"): TuiState {
  return {
    home, runs: [], selRun: 0, runId: null, mode: "plan",
    cases: [], selCase: 0, journaled: [], live: [], generation: 1,
    pane: "runs", palette: null, overlay: null, modal: null, elicitation: null,
    composer: "", help: false,
    status: { mode: "plan", runId: null, model, budgetUsed: 0, budgetLimit: null, egress: "none" },
  };
}

/** Fold one engine/local event into state. Pure except journal I/O (caller-owned). */
export function applyEvent(state: TuiState, ev: RpcEvent): TuiState {
  const next: TuiState = { ...state, live: [...state.live, ev] };
  const method = String((ev as { method?: string }).method ?? "");
  if (method === "approval.required") {
    const p = (ev.params ?? {}) as Record<string, unknown>;
    next.modal = {
      tool: String(p.tool ?? "unknown-tool"),
      destination: String(p.destination ?? "(undisclosed)"),
      method: String(p.method ?? "-"),
      requests: Number(p.requests ?? 1),
      sideEffects: String(p.sideEffects ?? "none stated"),
      permissionRef: String(p.permissionRef ?? p.permission_ref ?? "(missing)"),
      scope: typeof p.scope === "string" ? p.scope : undefined,
    };
  } else if (method === "elicitation.required") {
    const p = (ev.params ?? {}) as Record<string, unknown>;
    next.elicitation = {
      question: String(p.question ?? "input needed"),
      options: Array.isArray(p.options) ? p.options.map(String) : [],
    };
  }
  return next;
}

export type KeyOutcome = { quit?: boolean; notice?: string };

/** Pure key handling (tested without a TTY). */
export function applyAction(state: TuiState, action: Action): { state: TuiState; outcome: KeyOutcome } {
  const s = { ...state };
  const outcome: KeyOutcome = {};
  switch (action) {
    case "quit": outcome.quit = true; break;
    case "help": s.help = !s.help; break;
    case "up":
      if (s.pane === "runs") s.selRun = Math.max(0, s.selRun - 1);
      else if (s.pane === "queue") s.selCase = Math.max(0, s.selCase - 1);
      break;
    case "down":
      if (s.pane === "runs") s.selRun = Math.min(s.runs.length - 1, s.selRun + 1);
      else if (s.pane === "queue") s.selCase = Math.min(s.cases.length - 1, s.selCase + 1);
      break;
    case "left": s.pane = s.pane === "chat" ? "queue" : "runs"; break;
    case "right": s.pane = s.pane === "runs" ? "queue" : "chat"; break;
    case "select": {
      if (s.palette !== null) {
        const hits = PALETTE_ITEMS.filter((i) => i.includes(s.palette ?? ""));
        s.palette = null;
        if (hits[0] === "model inspect") outcome.notice = "inspect";
        else if (hits[0] === "evidence list") outcome.notice = "evidence";
        else if (hits[0] === "evidence diff") outcome.notice = "ediff";
        else if (hits[0] === "scope show") outcome.notice = "scope";
        else if (hits[0] === "coach") outcome.notice = "coach";
        else if (hits[0] === "coverage") outcome.notice = "coverage";
        else if (hits[0] === "case execute") outcome.notice = "execute";
        else s.composer = hits[0] ?? s.composer;
      } else if (s.pane === "runs" && s.runs[s.selRun]) {
        outcome.notice = `attach:${s.runs[s.selRun].run_id}`;
      }
      break;
    }
    case "palette": s.palette = s.palette === null ? "" : null; break;
    case "approve":
      if (s.modal) { s.modal = null; outcome.notice = "approved"; }
      break;
    case "deny":
      if (s.modal) { s.modal = null; outcome.notice = "denied"; }
      break;
    case "composer": outcome.notice = "composer"; break;
  }
  return { state: s, outcome };
}

export function renderState(state: TuiState, theme: Theme, width: number): string[] {
  const left = [
    ...renderRunSwitcher(state.runs, state.selRun, theme),
    "",
    ...renderQueue(state.cases, state.selCase, theme),
    "",
    ...renderEvidence([], theme),
  ];
  let right = renderConversation([...state.journaled, ...state.live], theme);
  if (state.overlay) {
    right = [paint(theme, "bold", state.overlay.title), ...state.overlay.lines,
             "  (esc/enter closes)", "", ...right];
  }
  if (state.modal) right = [...renderApproval(state.modal, width, theme), "", ...right];
  else if (state.elicitation) {
    right = [...renderElicitation(state.elicitation.question, state.elicitation.options, theme), "", ...right];
  }
  if (state.palette !== null) right = [...renderPalette(PALETTE_ITEMS, state.palette, theme), "", ...right];
  if (state.help) {
    right = [...right, "", ...renderHelp([
      "j/k or arrows: move · h/l: panes · enter: attach/select",
      "/: command palette · a/d: approve/deny · i: composer · ?: help · q: quit",
    ], theme)];
  }
  right = [...right, "", ...renderComposer(state.composer, theme)];
  return frame(`run ${state.runId ?? "(none)"} · gen ${state.generation}`, left, right, state.status, width, theme);
}

async function resolveRun(eng: EngineSession, runId: string | undefined): Promise<{ run_id: string; mode: string; status: string }> {
  if (runId) {
    const r = (await eng.request("run.status", { run_id: runId })) as { run_id?: string };
    if (!r?.run_id) throw new Error(`unknown run '${runId}'`);
    return r as { run_id: string; mode: string; status: string };
  }
  const list = (await eng.request("run.list")) as { runs: RunSummary[] };
  if (list.runs.length > 0) {
    const last = list.runs[list.runs.length - 1];
    return { run_id: last.run_id, mode: last.mode, status: last.status };
  }
  return (await eng.request("run.create", { mode: "plan" })) as { run_id: string; mode: string; status: string };
}

export async function runTui(opts: TuiOptions): Promise<number> {
  ensureHome(opts.home);
  const theme = opts.theme ?? resolveTheme(undefined, false);
  const eng = await opts.engine(opts.home);
  const state = createState(opts.home);
  try {
    const run = await resolveRun(eng, opts.runId);
    state.runId = run.run_id;
    state.mode = run.mode;
    state.status = { ...state.status, mode: run.mode, runId: run.run_id };
    const list = (await eng.request("run.list")) as { runs: RunSummary[] };
    state.runs = list.runs;
    state.selRun = Math.max(0, list.runs.findIndex((r) => r.run_id === run.run_id));
    state.cases = listCases(opts.home, run.run_id);

    // Crash-resume: replay the client journal first, then continue live.
    const replay = journalReplay(opts.home, run.run_id);
    state.journaled = replay.events;
    if (replay.events.length > 0) {
      opts.err(`resumed ${replay.events.length} journaled events (maxSeq=${replay.maxSeq})`);
    }
    const streamCount = opts.streamCount ?? 300;
    let live: RpcEvent[] = [];
    let resolveStream: () => void = () => {};
    const done = new Promise<void>((res) => { resolveStream = res; });
    eng.onEvent = ((ev: RpcEvent) => {
      // Fold modal-relevant events into state; keep the live stream append-only.
      const folded = applyEvent({ ...state, live }, ev);
      live = folded.live;
      state.modal = folded.modal;
      state.elicitation = folded.elicitation;
      journalAppend(opts.home, run.run_id, ev);
      if (!opts.plain && opts.isTTY) paint(state);
    }) as never;

    const sub = (eng.request("event.subscribe", { run_id: run.run_id, count: streamCount }, 120000) as Promise<unknown>)
      .catch((e) => { opts.err(`stream interrupted: ${(e as Error).message}`); })
      .finally(() => resolveStream());

    if (opts.plain || !opts.isTTY) {
      await sub;
      await done;
      for (const l of renderState({ ...state, journaled: replay.events, live }, theme, opts.width)) opts.out(l);
      return 0;
    }

    // Fullscreen loop.
    const stdin = opts.stdin;
    const keymap: Keymap = loadKeymap(opts.home);
    stdin.setRawMode(true);
    stdin.resume();
    paint(state);
    await new Promise<void>((resolve) => {
      const onData = (buf: Buffer) => {
        const key = keyName(buf);
        if (state.overlay !== null && (key === "esc" || key === "enter")) {
          state.overlay = null;
          paint(state);
          return;
        }
        if (state.palette !== null) {
          if (key === "esc") state.palette = null;
          else if (key === "enter") {
            const { state: ns } = applyAction(state, "select");
            Object.assign(state, ns);
          } else if (key === "backspace") {
            state.palette = state.palette.slice(0, -1);
          } else if (buf.length === 1 && buf[0] >= 32) {
            state.palette += buf.toString("utf8");
          }
          paint(state);
          return;
        }
        const action = actionFor(keymap, key) ?? (key === "enter" ? "select" : undefined);
        if (!action) return;
        const { state: ns, outcome } = applyAction(state, action);
        void handleOutcome(ns, outcome);
      };
      const handleOutcome = async (
        ns: TuiState, outcome: { quit?: boolean; notice?: string },
      ): Promise<void> => {
        Object.assign(state, ns);
        if (outcome.notice === "inspect" || outcome.notice === "evidence" || outcome.notice === "scope" ||
            outcome.notice === "coach" || outcome.notice === "coverage" ||
            outcome.notice === "execute" || outcome.notice === "ediff") {
          const which = outcome.notice;
          void (async () => {
            try {
              if (which === "inspect") {
                const list = (await eng.request("provider.list")) as {
                  providers: { name: string; transport?: string; type?: string }[];
                };
                const entries: InspectorEntry[] = [];
                for (const pr of list.providers) {
                  try {
                    const ml = (await eng.request("model.list", { provider: pr.name })) as {
                      models: string[];
                    };
                    const model = ml.models[0] ?? "default";
                    const probe = (await eng.request("model.probe",
                      { provider: pr.name, model })) as {
                      model?: string; verified_live?: boolean; credential?: string;
                      capabilities?: Record<string, unknown>; reachability?: string;
                    };
                    entries.push({
                      provider: pr.name, type: pr.type, transport: pr.transport,
                      model: probe.model ?? model, verified: probe.verified_live,
                      credential: probe.credential || undefined,
                      capabilities: probe.capabilities, reachability: probe.reachability,
                    });
                  } catch (e) {
                    entries.push({ provider: pr.name, reachability: `probe failed: ${(e as Error).message}` });
                  }
                }
                state.overlay = { title: "Model inspector", lines: renderModelInspector(entries, opts.theme) };
              } else if (which === "scope") {
                if (!state.runId) throw new Error("no run attached");
                const res = (await eng.request("scope.show", { run_id: state.runId })) as {
                  program_name?: string; mode?: string; policy_source?: string;
                  included_assets?: string[]; exclusions?: string[];
                  methods_allow?: string[]; methods_deny?: string[];
                  unresolved?: string[]; budgets?: Record<string, unknown>;
                };
                state.overlay = {
                  title: "Authorization & scope",
                  lines: renderScope(res, opts.theme),
                };
              } else if (which === "coach") {
                const sel = state.cases[state.selCase];
                if (!sel) throw new Error("no case selected in queue");
                const res = (await eng.request("coach.score",
                  { run_id: state.runId, case_id: sel.case_id })) as {
                  case_id?: string; scores?: Record<string, { score: number; reasons?: string[] }>;
                };
                state.overlay = {
                  title: `Coach: ${res.case_id ?? sel.case_id}`,
                  lines: renderCoach(res.case_id ?? sel.case_id, res.scores ?? {}, opts.theme),
                };
              } else if (which === "coverage") {
                const res = (await eng.request("coverage.report",
                  { run_id: state.runId })) as {
                  categories?: { id: string; checks: { status: string }[] }[];
                };
                state.overlay = {
                  title: "Coverage",
                  lines: renderCoverageSummary({ categories: res.categories ?? [] }, opts.theme),
                };
              } else if (which === "execute") {
                const sel = state.cases[state.selCase];
                if (!sel) throw new Error("no case selected in queue");
                const res = (await eng.request("case.execute", {
                  run_id: state.runId, case_id: sel.case_id, execute: true,
                }, 120000)) as {
                  proposal?: unknown; gaps?: string[]; decision?: { allowed?: boolean };
                  execution?: { sent?: boolean; status?: number; evidence_ids?: string[];
                    reasons?: string[]; approval_required?: boolean;
                    approval_preview?: Record<string, unknown>; transport_error?: string };
                };
                const ex = res.execution;
                if (ex?.approval_required && ex.approval_preview) {
                  const pv = ex.approval_preview;
                  state.modal = {
                    tool: "http.request", destination: String(pv.destination ?? ""),
                    method: String(pv.method ?? "GET"), requests: 1,
                    sideEffects: `case ${sel.case_id} one-shot`,
                    permissionRef: String(pv.permission ?? ""),
                    scope: typeof pv.scope === "string" ? pv.scope : undefined,
                  };
                } else {
                  state.overlay = {
                    title: `Execute ${sel.case_id}`,
                    lines: renderExecution({
                      sent: ex?.sent, allowed: ex ? ex.sent : res.decision?.allowed,
                      status: ex?.status, reasons: ex?.reasons,
                      approval_required: ex?.approval_required,
                      evidence_ids: ex?.evidence_ids,
                      transport_error: ex?.transport_error,
                    }, opts.theme),
                  };
                }
              } else if (which === "ediff") {
                const sel = state.cases[state.selCase];
                if (!sel) throw new Error("no case selected in queue");
                const shown = (await eng.request("case.show", {
                  run_id: state.runId, case_id: sel.case_id,
                })) as { case?: { evidence_refs?: string[] } };
                const refs = shown.case?.evidence_refs ?? [];
                if (refs.length < 2) throw new Error("case needs two evidence entries to diff");
                const diff = (await eng.request("evidence.diff", {
                  run_id: state.runId, baseline_id: refs[0], variant_id: refs[1],
                })) as { kind?: string; lines?: string[]; diffs?: unknown[] };
                const lines = diff.kind === "json"
                  ? (diff.diffs as unknown[]).slice(0, 30).map((d) => `  ${JSON.stringify(d).slice(0, 120)}`)
                  : (diff.lines ?? []).slice(0, 40).map((l) => `  ${l.slice(0, 120)}`);
                state.overlay = { title: `Diff ${refs[0]} vs ${refs[1]}`, lines };
              } else {
                if (!state.runId) throw new Error("no run attached");
                const res = (await eng.request("evidence.list", { run_id: state.runId })) as {
                  evidence: { evidence_id: string; source_type: string; byte_size: number;
                              redaction_state: string; description: string }[];
                };
                state.overlay = {
                  title: `Evidence (${res.evidence.length})`,
                  lines: renderEvidence(res.evidence.map((m) => ({
                    id: m.evidence_id,
                    desc: `[${m.source_type}/${m.redaction_state}] ${m.byte_size}B ${m.description}`,
                  })), opts.theme),
                };
              }
            } catch (e) {
              opts.err(`overlay failed: ${(e as Error).message}`);
            }
            paint(state);
          })();
          return;
        }
        if (outcome.notice === "approved" || outcome.notice === "denied") {
          const approved = outcome.notice === "approved";
          const scope = state.modal?.scope;
          try {
            if (scope) {
              await eng.request("approval.respond", {
                approved, run_id: state.runId, scope, kind: "one-shot",
              });
            } else {
              eng.notify("approval.respond", { approved, run_id: state.runId });
            }
          } catch { /* v0 engine: best effort */ }
          const decision = {
            jsonrpc: "2.0", method: "approval.decision",
            params: { approved, run_id: state.runId },
            protocol_version: "0.1.0", trace_id: "local", ts: new Date().toISOString(),
          } as unknown as RpcEvent;
          state.live = [...state.live, decision];
          if (state.runId) journalAppend(opts.home, state.runId, decision);
        } else if (outcome.notice === "composer") {
          stdin.setRawMode(false);
          stdin.pause();
          stdin.removeListener("data", onData);
          const output = { write: (s: string) => { opts.out(s.replace(/\n$/, "")); return true; } };
          const rl = createInterface({ input: stdin, output: output as unknown as NodeJS.WritableStream });
          rl.question("scopeforge> ", (answer) => {
            rl.close();
            state.composer = "";
            const you = {
              jsonrpc: "2.0", method: "chat.local",
              params: { text: answer, run_id: state.runId },
              protocol_version: "0.1.0", trace_id: "local", ts: new Date().toISOString(),
            } as unknown as RpcEvent;
            state.live = [...state.live, you];
            if (state.runId) journalAppend(opts.home, state.runId, you);
            // Agent turn (Phase 6): engine proposes, policy disposes. Fall back
            // to the local note if the model path is unavailable.
            void (async () => {
              if (!state.runId || !answer.trim()) {
                paint(state);
                return;
              }
              try {
                const turn = (await eng.request("chat.submit", {
                  run_id: state.runId, message: answer,
                }, 60000)) as { role?: string; output?: unknown; gaps?: string[] };
                const reply = {
                  jsonrpc: "2.0", method: "agent.turn",
                  params: { role: turn.role ?? "agent", text: summarizeTurn(turn),
                            run_id: state.runId },
                  protocol_version: "0.1.0", trace_id: "local", ts: new Date().toISOString(),
                } as unknown as RpcEvent;
                state.live = [...state.live, reply];
                journalAppend(opts.home, state.runId as string, reply);
              } catch (e) {
                opts.err(`agent turn failed: ${(e as Error).message}`);
              }
              paint(state);
            })();
            stdin.setRawMode(true);
            stdin.resume();
            stdin.on("data", onData);
            paint(state);
          });
          return;
        } else if (outcome.notice?.startsWith("attach:")) {
          const id = outcome.notice.slice("attach:".length);
          void (async () => {
            try {
              const r = (await eng.request("run.status", { run_id: id })) as {
                run_id: string; mode: string;
              };
              const replayed = journalReplay(opts.home, id);
              state.runId = r.run_id;
              state.mode = r.mode;
              state.status = { ...state.status, mode: r.mode, runId: r.run_id };
              state.cases = listCases(opts.home, id);
              state.journaled = replayed.events;
              state.live = [];
              state.generation += 1;
              try {
                eng.notify("event.subscribe", { run_id: id, count: streamCount });
              } catch { /* display continues on journal */ }
            } catch (e) {
              opts.err(`attach failed: ${(e as Error).message}`);
            }
            paint(state);
          })();
          return;
        }
        if (outcome.quit) {
          stdin.setRawMode(false);
          stdin.pause();
          stdin.removeListener("data", onData);
          resolve();
          return;
        }
        paint(state);
      };
      stdin.on("data", onData);
      void done.then(() => paint(state));
    });
    function paint(s: TuiState): void {
      opts.out("\u001b[2J\u001b[H" + renderState({ ...s, live }, theme, opts.width).join("\n"));
    }
    await sub;
    return 0;
  } finally {
    try {
      await eng.stop();
    } catch { /* best effort */ }
    try {
      if (opts.stdin.isTTY) opts.stdin.setRawMode(false);
    } catch { /* best effort */ }
  }
}
