// CLI command surface (plan.md §4.2, Phase-2 slice). Hand-rolled parser:
// zero dependencies, deterministic `--json` / `--no-color` handling, stable
// exit codes. Full agent/case/provider/tool commands land per-phase; the whole
// documented tree parses today so `--help` is honest about what exists.
import { EngineClient } from "@scopeforge/protocol";
import { validateCase } from "./case-store.js";
import { EXIT } from "./exit-codes.js";
import { ensureHome, homeDir, readConfig, runDir } from "./home.js";
import { journalAppend, journalReplay } from "./journal.js";
import { checkProtocolCompat, verifyEngine } from "./launcher.js";
import {
  validateProviderConfig,
  writeProvider,
  type ProviderType,
} from "./providers-toml.js";
import { validateScope } from "./scope.js";
import { resolveTheme, stripAnsi } from "./themes.js";
import { runTui } from "./tui.js";

export const CLI_VERSION = "0.1.0";
export const PROTOCOL_VERSION_CLI = "0.1.0";

export interface CliContext {
  cwd: string;
  env: NodeJS.ProcessEnv;
  isTTY: boolean;
  columns?: number;
  out: (s: string) => void;
  err: (s: string) => void;
  engine?: EngineFactory;
}

export type EngineFactory = (home: string) => Promise<EngineSession>;

export interface EngineSession {
  request: (method: string, params?: Record<string, unknown>, timeoutMs?: number) => Promise<unknown>;
  notify: (method: string, params?: Record<string, unknown>) => void;
  onEvent: ((e: never) => void) | undefined;
  stop: () => Promise<void>;
}

export function defaultEngineEntry(): string {
  return new URL(
    "../../../services/engine/src/scopeforge_engine/server.py",
    import.meta.url,
  ).pathname;
}

export async function defaultEngine(home: string): Promise<EngineSession> {
  // Phase 9: launcher verifies engine integrity + protocol before spawn (fail closed)
  const enginePath = defaultEngineEntry();
  const v = verifyEngine(enginePath, process.env.SCOPEFORGE_ENGINE_SHA256);
  if (!v.ok) throw new Error(v.error);
  const compat = checkProtocolCompat(enginePath, Number(PROTOCOL_VERSION_CLI.split(".")[0]));
  if (!compat.ok) throw new Error(compat.error);
  const c = new EngineClient("python3", [enginePath], { env: { SCOPEFORGE_HOME: home } });
  c.start();
  await c.initialize("terminal/0.1.0");
  return {
    request: (m, p, t) => c.request(m, p ?? {}, t),
    notify: (m, p) => c.notify(m, p ?? {}),
    get onEvent() {
      return c.onEvent as never;
    },
    set onEvent(f: never) {
      c.onEvent = f as never;
    },
    stop: () => c.stop(),
  };
}

interface Parsed {
  path: string[];
  flags: Map<string, string | boolean>;
  positionals: string[];
  json: boolean;
  noColor: boolean;
  yes: boolean;
  home: string | undefined;
  help: boolean;
}

function parseArgs(argv: string[]): Parsed {
  const flags = new Map<string, string | boolean>();
  const positionals: string[] = [];
  const path: string[] = [];
  let i = 0;
  for (; i < argv.length; i++) {
    const a = argv[i];
    if (a === "--") {
      positionals.push(...argv.slice(i + 1));
      break;
    }
    if (a.startsWith("--")) {
      const eq = a.indexOf("=");
      if (eq >= 0) flags.set(a.slice(2, eq), a.slice(eq + 1));
      else if (i + 1 < argv.length && !argv[i + 1].startsWith("--") && flagTakesValue(a.slice(2))) {
        flags.set(a.slice(2), argv[++i]);
      } else flags.set(a.slice(2), true);
    } else if (a.startsWith("-") && a.length > 1) {
      for (const ch of a.slice(1)) flags.set(ch, true);
    } else if (path.length < 2) {
      path.push(a);
    } else {
      positionals.push(a);
    }
  }
  const g = (k: string) => flags.get(k);
  return {
    path, flags, positionals,
    json: g("json") === true || g("j") === true,
    noColor: g("no-color") === true || g("n") === true,
    yes: g("yes") === true || g("y") === true,
    home: typeof g("home") === "string" ? (g("home") as string) : undefined,
    help: g("help") === true || g("h") === true,
  };
}

async function cmdRunStartLab(ctx: CliContext, p: Parsed, home: string, lab: string): Promise<number> {
  ensureHome(home);
  try {
    return await withEngine(ctx, home, async (e) => {
      const launched = (await e.request("lab.launch", { lab })) as {
        name: string; url: string; manifest: { budgets?: Record<string, number> };
      };
      const port = Number(new URL(launched.url).port);
      const budgets = { requests_total: 50, max_concurrency: 1, ...(launched.manifest.budgets ?? {}) };
      const created = (await e.request("run.create", {
        mode: "live",
        engagement: {
          program_name: `${launched.name} (local lab)`,
          authorization_basis: "researcher-owned local fixtures; loopback only",
          included_assets: [`http://127.0.0.1:${port}`],
        },
      })) as { run_id: string };
      await e.request("policy.import", {
        run_id: created.run_id,
        policy: {
          program_name: `${launched.name} (local lab)`,
          policy_source: `lab:${launched.name}`,
          mode: "live",
          include: [{ host: "127.0.0.1", port, scheme: "http", allow_private: true }],
          methods_allow: ["GET", "HEAD", "POST"],
          budgets,
        },
      });
      emit(ctx, p, { ok: true, run_id: created.run_id, lab: launched.name, url: launched.url, budgets }, [
        `run ${created.run_id} [live] scoped to lab '${launched.name}' at ${launched.url}`,
        "loopback only; approvals still required for state changes; budgets enforced",
      ]);
      return EXIT.OK;
    });
  } catch (err) {
    return fail(ctx, p, EXIT.FAIL, (err as Error).message);
  }
}

function flagTakesValue(name: string): boolean {
  return new Set([
    "run", "mode", "check", "hypothesis", "expected-rule", "baseline", "changed",
    "observable", "stopping", "permission", "asset", "result", "program", "count",
    "format", "home", "theme", "capability", "name", "type", "base-url", "credential",
    "model", "provider", "source-type", "mime", "description", "finding-file", "lab",
    "executable", "args", "target-url", "command",
  ]).has(name);
}

const str = (p: Parsed, k: string): string | undefined => {
  const v = p.flags.get(k);
  return typeof v === "string" ? v : undefined;
};

// Commands scheduled for later phases: parse + explain, never pretend.
const STUBS: Record<string, string> = {
  "eval run": "Phase 6+ (evaluation corpus)",
};

const HELP = `scopeforge ${CLI_VERSION} — authorized research harness (plan/artifacts/live)

usage: scopeforge [--json] [--no-color] [--home DIR] <command> [args]

  init                          scaffold .scopeforge home
  doctor                        check node, python, engine, protocol
  tui [--run ID] [--plain]      interactive shell (plain backend, ADR-04)
  provider add --name N --type T [--base-url U] [--credential env:VAR]
  provider test PROVIDER [--model M] [--no-live]
  models list [--provider P] | inspect PROVIDER:MODEL
  run start --mode plan|artifacts|live | --lab tenancy-demo
  run list | status | resume | cancel | close --run ID
  case new --check T02.1 ... --run ID
  case list | show | retry --run ID
  artifact import FILE... --run ID
  har import FILE --run ID | burp import FILE --run ID
  evidence show EID --run ID [--preview] | redact EID --run ID
  report build --run ID --finding-file F.json
  export RUN_ID [--include-originals]
  program import POLICY_FILE [--run ID]
  scope show --run ID | validate HOST_OR_URL [--run ID]
  mcp add NAME COMMAND | list | enable NAME | disable NAME
  tool list | invoke --name N --executable PATH --args A [--target-url U]

exit codes: 0 ok · 1 fail · 2 usage/config · 3 not implemented (phase noted)
            4 policy blocked · 5 approval declined · 6 provider · 7 tool
            8 budget exhausted · 9 partial/inconclusive
notes: --yes never bypasses live-target, credential, destructive, or policy gates.
       mode badge (plan/artifacts/live) is always shown before live-affecting output.`;

export async function runCli(argv: string[], ctx: CliContext): Promise<number> {
  const p = parseArgs(argv);
  const home = p.home ?? homeDir(ctx.cwd, ctx.env);
  if (p.path.length === 0 || p.help) {
    if (p.path.length > 0 && !p.help) return fail(ctx, p, EXIT.USAGE, "no command given");
    ctx.out(p.json ? JSON.stringify({ version: CLI_VERSION, help: HELP }) : HELP);
    return EXIT.OK;
  }
  if (p.path[0] === "--version" || p.path[0] === "version") {
    ctx.out(p.json ? JSON.stringify({ version: CLI_VERSION }) : CLI_VERSION);
    return EXIT.OK;
  }
  const key2 = p.path.slice(0, 2).join(" ");
  const key1 = p.path[0];
  if (STUBS[key2] ?? STUBS[key1]) {
    const phase = STUBS[key2] ?? STUBS[key1];
    const msg = `'${key2 in STUBS ? key2 : key1}' is scheduled for ${phase}. See plan.md.`;
    if (p.json) ctx.out(JSON.stringify({ version: CLI_VERSION, implemented: false, phase, command: key2 }));
    else ctx.err(`not implemented: ${msg}`);
    return EXIT.NOT_IMPLEMENTED;
  }

  const [cmd, sub] = p.path;
  try {
    if (cmd === "init") return cmdInit(ctx, p, home);
    if (cmd === "doctor") return await cmdDoctor(ctx, p, home);
    if (cmd === "tui") return await cmdTui(ctx, p, home);
    if (cmd === "provider") return await cmdProvider(ctx, p, home, sub);
    if (cmd === "models") return await cmdModels(ctx, p, home, sub);
    if (cmd === "run") return await cmdRun(ctx, p, home, sub, p.positionals);
    if (cmd === "case") return await cmdCase(ctx, p, home, sub);
    if (cmd === "artifact") return await cmdArtifact(ctx, p, home, sub);
    if (cmd === "evidence") return await cmdEvidence(ctx, p, home, sub);
    if (cmd === "report") return await cmdReport(ctx, p, home, sub);
    if (cmd === "export") return await cmdExport(ctx, p, home);
    if (cmd === "program" && sub === "import") return await cmdProgramImport(ctx, p, home);
    if (cmd === "scope") return await cmdScope(ctx, p, home, sub);
    if (cmd === "mcp") return await cmdMcp(ctx, p, home, sub);
    if (cmd === "tool") return await cmdTool(ctx, p, home, sub);
    if (cmd === "har" && sub === "import") return await cmdHarImport(ctx, p, home, "har");
    if (cmd === "burp" && sub === "import") return await cmdHarImport(ctx, p, home, "burp");
    return fail(ctx, p, EXIT.USAGE, `unknown command '${p.path.join(" ")}'`, HELP);
  } catch (e) {
    return fail(ctx, p, EXIT.FAIL, `internal error: ${(e as Error).message}`);
  }
}

function fail(ctx: CliContext, p: Parsed, code: number, message: string, extra?: string): number {
  if (p.json) ctx.out(JSON.stringify({ version: CLI_VERSION, ok: false, error: message }));
  else {
    ctx.err(`error: ${message}`);
    if (extra) ctx.err(extra);
  }
  return code;
}

function emit(ctx: CliContext, p: Parsed, obj: unknown, lines: string[]): void {
  if (p.json) ctx.out(JSON.stringify({ version: CLI_VERSION, ...(obj as Record<string, unknown>) }));
  else for (const l of lines) ctx.out(p.noColor ? stripAnsi(l) : l);
}

function themeOf(ctx: CliContext, p: Parsed, home: string) {
  const cfg = readConfig(home);
  return resolveTheme(cfg.theme, p.noColor || !ctx.isTTY);
}

// -- init / doctor --------------------------------------------------------

function cmdInit(ctx: CliContext, p: Parsed, home: string): number {
  ensureHome(home);
  emit(ctx, p, { ok: true, home }, [
    `initialized ${home}`,
    "next: scopeforge doctor, then `run start --mode plan` (zero target egress)",
  ]);
  return EXIT.OK;
}

async function cmdDoctor(ctx: CliContext, p: Parsed, home: string): Promise<number> {
  const checks: { name: string; ok: boolean; detail: string; severity: "error" | "warn" }[] = [];
  const nodeOk = Number(process.versions.node.split(".")[0]) >= 20;
  checks.push({ name: "node", ok: nodeOk, detail: process.versions.node, severity: "error" });
  let pyDetail = "missing";
  try {
    const { spawnSync } = await import("node:child_process");
    const r = spawnSync("python3", ["--version"], { encoding: "utf8" });
    pyDetail = String(r.stdout || r.stderr || "").trim() || "missing";
  } catch {
    /* missing */
  }
  // The v0–v2 spine is stdlib-only and runs on 3.11; 3.12+ is required from
  // Phase 4 (Pydantic/anyio/httpx), so this is a warning, not a gate.
  const pyOk = /3\.(1[2-9]|[2-9]\d)/.test(pyDetail);
  checks.push({
    name: "python3", ok: pyOk,
    detail: pyOk ? pyDetail : `${pyDetail} (3.12+ required from Phase 4; spine runs)`,
    severity: "warn",
  });
  let engineDetail = "unreachable";
  let engineOk = false;
  try {
    const eng = await (ctx.engine ?? defaultEngine)(home);
    try {
      const init = (await eng.request("initialize", {
        protocol_version: PROTOCOL_VERSION_CLI, client: `terminal/${CLI_VERSION}`,
      })) as { protocol_version: string };
      engineOk = init.protocol_version === PROTOCOL_VERSION_CLI;
      engineDetail = `protocol ${init.protocol_version}`;
    } finally {
      await eng.stop();
    }
  } catch (e) {
    engineDetail = (e as Error).message;
  }
  checks.push({ name: "engine", ok: engineOk, detail: engineDetail, severity: "error" });
  const ok = checks.every((c) => c.ok || c.severity === "warn");
  const lines = checks.map((c) => `  ${(c.ok ? "PASS" : c.severity === "warn" ? "WARN" : "FAIL")} ${c.name}: ${c.detail}`);
  emit(ctx, p, { ok, checks }, [ok ? "doctor: all checks pass" : "doctor: FAILURES present", ...lines]);
  return ok ? EXIT.OK : EXIT.FAIL;
}

// -- tui -------------------------------------------------------------------

async function cmdTui(ctx: CliContext, p: Parsed, home: string): Promise<number> {
  const plain = p.flags.get("plain") === true;
  const once = p.flags.get("once") === true;
  const runId = str(p, "run");
  if (!ctx.isTTY && !plain && !once) {
    return fail(ctx, p, EXIT.USAGE,
      "tui needs a TTY; use --plain (screen-reader/pipe friendly) or --once, or redirect with --json commands");
  }
  if (p.yes) ctx.err("note: --yes has no effect on tui; live/approval gates always apply");
  return runTui({
    home, runId, plain: plain || once, once,
    width: ctx.columns ?? 100,
    theme: themeOf(ctx, p, home),
    out: ctx.out, err: ctx.err,
    stdin: process.stdin, isTTY: ctx.isTTY,
    engine: ctx.engine ?? defaultEngine,
  });
}

// -- run -------------------------------------------------------------------

async function withEngine<T>(ctx: CliContext, home: string, fn: (e: EngineSession) => Promise<T>): Promise<T> {
  const eng = await (ctx.engine ?? defaultEngine)(home);
  try {
    return await fn(eng);
  } finally {
    await eng.stop();
  }
}

async function cmdRun(
  ctx: CliContext, p: Parsed, home: string, sub: string | undefined, pos: string[],
): Promise<number> {
  ensureHome(home);
  if (sub === "start") {
    const lab = str(p, "lab");
    if (lab) return cmdRunStartLab(ctx, p, home, lab);
    const mode = str(p, "mode") ?? pos[0];
    if (mode !== "plan" && mode !== "artifacts" && mode !== "live") {
      return fail(ctx, p, EXIT.USAGE, "--mode must be plan|artifacts|live");
    }
    if (mode === "live") {
      // Phase-5 gate: live needs the policy review screen, which does not exist yet.
      return fail(ctx, p, EXIT.POLICY_BLOCKED,
        "live mode refused: scope review + policy gate land in Phase 5 (plan.md §7). Use plan|artifacts.");
    }
    const program = str(p, "program");
    const res = await withEngine(ctx, home, (e) =>
      e.request("run.create", { mode, ...(program ? { program } : {}) }),
    ) as { run_id: string; mode: string; status: string };
    emit(ctx, p, { ok: true, ...res }, [
      `run ${res.run_id} started [${res.mode}] status=${res.status} — no target egress in this mode`,
    ]);
    return EXIT.OK;
  }
  if (sub === "list") {
    const res = await withEngine(ctx, home, (e) => e.request("run.list")) as {
      runs: { run_id: string; mode: string; status: string; events: number }[];
    };
    emit(ctx, p, { ok: true, ...res }, res.runs.length === 0
      ? ["(no runs yet — `run start --mode plan`)"]
      : res.runs.map((r) => `${r.run_id} [${r.mode}] ${r.status} ev=${r.events}`));
    return EXIT.OK;
  }
  const runId = str(p, "run") ?? pos[0];
  if (!runId && (sub === "status" || sub === "resume" || sub === "cancel" || sub === "close")) {
    return fail(ctx, p, EXIT.USAGE, `${sub} requires --run ID`);
  }
  // Unknown run_id surfaces as JSON-RPC -32602: map to a clean FAIL, not a crash.
  const req = async (eng: EngineSession, method: string, params: Record<string, unknown>) => {
    try {
      return await eng.request(method, params);
    } catch (e) {
      if ((e as { code?: number }).code === -32602) return { __unknown: true };
      throw e;
    }
  };
  if (sub === "status") {
    const res = await withEngine(ctx, home, (e) => req(e, "run.status", { run_id: runId })) as Record<string, unknown>;
    if (!res || (res as { run_id?: string }).run_id === undefined) {
      return fail(ctx, p, EXIT.FAIL, `unknown run '${runId}'`);
    }
    const r = res as { run_id: string; mode: string; status: string; events: number; last_seq: number };
    emit(ctx, p, { ok: true, ...res }, [
      `${r.run_id} [${r.mode}] ${r.status} ev=${r.events} last_seq=${r.last_seq}`,
    ]);
    return EXIT.OK;
  }
  if (sub === "resume") {
    const res = await withEngine(ctx, home, (e) => req(e, "run.status", { run_id: runId })) as {
      run_id?: string; mode?: string; status?: string;
    };
    if (!res?.run_id) return fail(ctx, p, EXIT.FAIL, `unknown run '${runId}'`);
    const { events, maxSeq } = journalReplay(home, runId as string);
    emit(ctx, p, { ok: true, run_id: runId, replayed: events.length, maxSeq }, [
      `resumed ${runId} [${res.mode}] ${res.status}: replayed ${events.length} journaled events (maxSeq=${maxSeq})`,
    ]);
    return EXIT.OK;
  }
  if (sub === "cancel" || sub === "close") {
    const method = sub === "cancel" ? "run.cancel" : "run.close";
    const res = await withEngine(ctx, home, (e) => req(e, method, { run_id: runId })) as Record<string, unknown>;
    if ((res as { error?: unknown }).error || (res as { __unknown?: boolean }).__unknown) {
      return fail(ctx, p, EXIT.FAIL, `unknown run '${runId}'`);
    }
    emit(ctx, p, { ok: true, ...res }, [`${runId}: ${sub}led`]);
    return EXIT.OK;
  }
  return fail(ctx, p, EXIT.USAGE, `unknown run subcommand '${sub ?? ""}' (start|list|status|resume|cancel|close)`);
}

// -- case ------------------------------------------------------------------

function caseKebab(p: Parsed): Record<string, string | undefined> {
  return {
    check_id: str(p, "check"),
    hypothesis: str(p, "hypothesis"),
    expected_rule: str(p, "expected-rule"),
    baseline: str(p, "baseline"),
    changed_variable: str(p, "changed"),
    observable_result: str(p, "observable"),
    stopping_point: str(p, "stopping"),
    permission_ref: str(p, "permission"),
    asset: str(p, "asset"),
    feature: str(p, "feature"),
  };
}

// Cases are engine-owned (durable store); validateCase pre-checks client-side
// for fast usage errors, and case-store.ts remains the compatible file reader.
async function cmdCase(
  ctx: CliContext, p: Parsed, home: string, sub: string | undefined,
): Promise<number> {
  ensureHome(home);
  const runId = str(p, "run") ?? p.positionals[1];
  if (sub === "new") {
    if (!runId) return fail(ctx, p, EXIT.USAGE, "case new requires --run ID");
    const data = { ...(Object.fromEntries(Object.entries(caseKebab(p)).filter(([, v]) => v !== undefined)) as Record<string, string>) };
    const errors = validateCase(data);
    if (errors.length > 0) return fail(ctx, p, EXIT.USAGE, `invalid case: ${errors.join("; ")}`);
    try {
      const res = await withEngine(ctx, home, (e) =>
        e.request("case.create", { run_id: runId, case: data }),
      ) as { case: { case_id: string; check_id: string } };
      emit(ctx, p, { ok: true, case: res.case }, [
        `created ${res.case.case_id} (${res.case.check_id}) queued — lead is not a finding`,
      ]);
      return EXIT.OK;
    } catch (err) {
      return fail(ctx, p, EXIT.FAIL, engineError(err, runId));
    }
  }
  if (sub === "list") {
    if (!runId) return fail(ctx, p, EXIT.USAGE, "case list requires --run ID");
    try {
      const params: Record<string, unknown> = { run_id: runId };
      const result = str(p, "result");
      if (result) params.result = result;
      const res = await withEngine(ctx, home, (e) => e.request("case.list", params)) as {
        cases: { case_id: string; check_id: string; result: string; hypothesis: string }[];
      };
      emit(ctx, p, { ok: true, cases: res.cases }, res.cases.length === 0
        ? ["(no cases)"]
        : res.cases.map((c) => `${c.case_id} ${c.check_id} ${c.result} ${String(c.hypothesis).slice(0, 70)}`));
      return EXIT.OK;
    } catch (err) {
      return fail(ctx, p, EXIT.FAIL, engineError(err, runId));
    }
  }
  if (sub === "show" || sub === "retry") {
    const caseId = p.positionals[0];
    if (!runId || !caseId) return fail(ctx, p, EXIT.USAGE, `case ${sub} CASE_ID --run ID`);
    try {
      if (sub === "retry") {
        const res = await withEngine(ctx, home, (e) =>
          e.request("case.update", { run_id: runId, case_id: caseId, fields: { result: "queued" } }),
        ) as { case: { case_id: string } };
        emit(ctx, p, { ok: true, case_id: res.case.case_id, result: "queued" }, [`${caseId} re-queued`]);
        return EXIT.OK;
      }
      const res = await withEngine(ctx, home, (e) =>
        e.request("case.show", { run_id: runId, case_id: caseId }),
      ) as { case: Record<string, string> };
      const c = res.case;
      emit(ctx, p, { ok: true, case: c }, [
        `${c.case_id} ${c.check_id} ${c.result}`,
        `hypothesis: ${c.hypothesis}`,
        `rule: ${c.expected_rule}`,
        `baseline -> variant: ${c.baseline} -> ${c.changed_variable}`,
        `expect: ${c.observable_result}`,
        `stop: ${c.stopping_point}`,
      ]);
      return EXIT.OK;
    } catch (err) {
      return fail(ctx, p, EXIT.FAIL, engineError(err, (caseId ?? runId) as string));
    }
  }
  return fail(ctx, p, EXIT.USAGE, `unknown case subcommand '${sub ?? ""}' (new|list|show|retry)`);
}

/** Map engine -32602 (unknown run/case/evidence) to a clean message. */
function engineError(err: unknown, id: string): string {
  if ((err as { code?: number }).code === -32602) return `unknown id '${id}'`;
  return (err as Error).message;
}

async function cmdArtifact(
  ctx: CliContext, p: Parsed, home: string, sub: string | undefined,
): Promise<number> {
  ensureHome(home);
  if (sub !== "import") return fail(ctx, p, EXIT.USAGE, "usage: artifact import FILE... --run ID");
  const runId = str(p, "run");
  if (!runId) return fail(ctx, p, EXIT.USAGE, "artifact import requires --run ID");
  if (p.positionals.length === 0) return fail(ctx, p, EXIT.USAGE, "artifact import needs at least one FILE");
  try {
    const out = await withEngine(ctx, home, async (e) => {
      const ids: { file: string; evidence_id: string; sha256: string }[] = [];
      for (const file of p.positionals) {
        const res = (await e.request("artifact.import", {
          run_id: runId, path: file,
          source_type: str(p, "source-type") ?? "file",
          mime: str(p, "mime") ?? "", description: str(p, "description") ?? "",
        })) as { evidence_id: string; sha256: string };
        ids.push({ file, ...res });
      }
      return ids;
    });
    emit(ctx, p, { ok: true, imported: out },
      out.map((r) => `${r.file} -> ${r.evidence_id} sha256:${r.sha256.slice(0, 12)}`));
    return EXIT.OK;
  } catch (err) {
    return fail(ctx, p, EXIT.FAIL, engineError(err, runId));
  }
}

async function cmdEvidence(
  ctx: CliContext, p: Parsed, home: string, sub: string | undefined,
): Promise<number> {
  ensureHome(home);
  const runId = str(p, "run");
  if (!runId) return fail(ctx, p, EXIT.USAGE, `evidence ${sub ?? ""} requires --run ID`);
  try {
    if (sub === "redact") {
      const eid = p.positionals[0];
      if (!eid) return fail(ctx, p, EXIT.USAGE, "evidence redact EVIDENCE_ID --run ID");
      const res = await withEngine(ctx, home, (e) =>
        e.request("evidence.redact", { run_id: runId, evidence_id: eid }),
      ) as { derived: { evidence_id: string } };
      emit(ctx, p, { ok: true, derived: res.derived }, [
        `${eid} redacted -> ${res.derived.evidence_id} (original immutable)`,
      ]);
      return EXIT.OK;
    }
    if (sub === "show") {
      const eid = p.positionals[0];
      if (eid) {
        const res = await withEngine(ctx, home, (e) =>
          e.request("evidence.show", {
            run_id: runId, evidence_id: eid, preview: p.flags.get("preview") === true,
          }),
        ) as { evidence: Record<string, unknown>; preview?: string };
        const m = res.evidence;
        emit(ctx, p, { ok: true, ...res }, [
          `${m.evidence_id} [${m.source_type}] sha256:${String(m.sha256).slice(0, 12)} ${m.byte_size}B`,
          `captured: ${m.captured_at} tool: ${m.tool} redaction: ${m.redaction_state}`,
          ...(res.preview !== undefined ? ["--- preview ---", String(res.preview)] : []),
        ]);
        return EXIT.OK;
      }
      const res = await withEngine(ctx, home, (e) =>
        e.request("evidence.list", { run_id: runId }),
      ) as { evidence: Record<string, unknown>[] };
      emit(ctx, p, { ok: true, evidence: res.evidence }, res.evidence.length === 0
        ? ["(no evidence)"]
        : res.evidence.map((m) =>
            `${m.evidence_id} [${m.source_type}] ${m.byte_size}B ${String(m.description).slice(0, 60)}`));
      return EXIT.OK;
    }
  } catch (err) {
    return fail(ctx, p, EXIT.FAIL, engineError(err, runId));
  }
  return fail(ctx, p, EXIT.USAGE, `unknown evidence subcommand '${sub ?? ""}' (show|redact)`);
}

async function cmdReport(
  ctx: CliContext, p: Parsed, home: string, sub: string | undefined,
): Promise<number> {
  ensureHome(home);
  if (sub !== "build") return fail(ctx, p, EXIT.USAGE, "usage: report build --run ID --finding-file F.json");
  const runId = str(p, "run");
  const file = str(p, "finding-file");
  if (!runId || !file) return fail(ctx, p, EXIT.USAGE, "report build requires --run ID --finding-file F.json");
  let finding: unknown;
  try {
    const { readFileSync } = await import("node:fs");
    finding = JSON.parse(readFileSync(file, "utf8"));
  } catch (err) {
    return fail(ctx, p, EXIT.USAGE, `cannot read finding file: ${(err as Error).message}`);
  }
  try {
    const res = await withEngine(ctx, home, (e) =>
      e.request("report.build", { run_id: runId, finding }),
    ) as { finding_id: string; path: string };
    emit(ctx, p, { ok: true, ...res }, [`draft ${res.finding_id} written: ${res.path}`]);
    return EXIT.OK;
  } catch (err) {
    return fail(ctx, p, (err as { code?: number }).code === -32602 ? EXIT.USAGE : EXIT.FAIL,
      (err as Error).message);
  }
}

async function cmdExport(ctx: CliContext, p: Parsed, home: string): Promise<number> {
  ensureHome(home);
  const runId = p.positionals[0] ?? p.path[1] ?? str(p, "run");
  if (!runId) return fail(ctx, p, EXIT.USAGE, "usage: export RUN_ID [--include-originals]");
  try {
    const res = await withEngine(ctx, home, (e) =>
      e.request("export.create", {
        run_id: runId, include_originals: p.flags.get("include-originals") === true,
      }),
    ) as { path: string; sha256: string; files: number; warnings: string[] };
    emit(ctx, p, { ok: true, ...res }, [
      `exported ${res.files} files: ${res.path}`,
      `sha256:${res.sha256.slice(0, 16)}`,
      ...res.warnings.map((w) => `warning: ${w}`),
    ]);
    return EXIT.OK;
  } catch (err) {
    return fail(ctx, p, EXIT.FAIL, engineError(err, runId));
  }
}

async function cmdMcp(ctx: CliContext, p: Parsed, home: string, sub: string | undefined): Promise<number> {
  ensureHome(home);
  try {
    if (sub === "add") {
      const name = p.positionals[0] ?? str(p, "name");
      const command = p.positionals[1] ?? str(p, "command");
      if (!name || !command) return fail(ctx, p, EXIT.USAGE, "usage: mcp add NAME COMMAND");
      const res = await withEngine(ctx, home, (e) => e.request("mcp.add", { name, command })) as { name: string };
      emit(ctx, p, { ok: true, ...res }, [`mcp server '${res.name}' added (disabled by default — review before enable)`]);
      return EXIT.OK;
    }
    if (sub === "list" || !sub) {
      const res = await withEngine(ctx, home, (e) => e.request("mcp.list", {})) as { servers: { name: string; enabled: boolean; command: string }[] };
      emit(ctx, p, { ok: true, ...res }, res.servers.length === 0 ? ["(no mcp servers)"] : res.servers.map((s) => `${s.name} [${s.enabled ? "enabled" : "disabled"}] ${s.command}`));
      return EXIT.OK;
    }
    if (sub === "enable" || sub === "disable" || sub === "remove") {
      const name = p.positionals[0];
      if (!name) return fail(ctx, p, EXIT.USAGE, `usage: mcp ${sub} NAME`);
      const method = `mcp.${sub}` as const;
      const res = await withEngine(ctx, home, (e) => e.request(method, { name })) as Record<string, unknown>;
      emit(ctx, p, { ok: true, ...res }, [`mcp ${sub}: ${name}`]);
      return EXIT.OK;
    }
  } catch (err) {
    return fail(ctx, p, EXIT.FAIL, (err as Error).message);
  }
  return fail(ctx, p, EXIT.USAGE, `unknown mcp subcommand '${sub ?? ""}' (add|list|enable|disable|remove)`);
}

async function cmdTool(ctx: CliContext, p: Parsed, home: string, sub: string | undefined): Promise<number> {
  ensureHome(home);
  try {
    if (sub === "list" || !sub) {
      const res = await withEngine(ctx, home, (e) => e.request("tool.list", {})) as { tools: { name: string; risk_class: string }[] };
      emit(ctx, p, { ok: true, ...res }, res.tools.map((t) => `${t.name} [${t.risk_class}]`));
      return EXIT.OK;
    }
    if (sub === "invoke") {
      const name = str(p, "name") ?? p.positionals[0];
      const exe = str(p, "executable");
      if (!name || !exe) return fail(ctx, p, EXIT.USAGE, "usage: tool invoke --name N --executable PATH [--args A] [--target-url U] [--run ID]");
      const argsStr = str(p, "args") ?? "";
      const args = argsStr ? argsStr.split(" ") : p.positionals.slice(1);
      const res = await withEngine(ctx, home, (e) => e.request("tool.invoke", {
        name, executable: exe, args, target_url: str(p, "target-url") ?? "", run_id: str(p, "run") ?? "",
      })) as { tool: string; exit_code: number; stdout: string; truncated?: boolean };
      emit(ctx, p, { ok: true, ...res }, [`${res.tool} exit:${res.exit_code}${res.truncated ? " (truncated)" : ""}`, res.stdout.slice(0, 500)]);
      return res.exit_code === 0 ? EXIT.OK : EXIT.TOOL;
    }
  } catch (err) {
    return fail(ctx, p, EXIT.FAIL, (err as Error).message);
  }
  return fail(ctx, p, EXIT.USAGE, `unknown tool subcommand '${sub ?? ""}' (list|invoke)`);
}

async function cmdHarImport(ctx: CliContext, p: Parsed, home: string, kind: "har" | "burp"): Promise<number> {
  ensureHome(home);
  const runId = str(p, "run");
  const file = p.positionals[0];
  if (!runId || !file) return fail(ctx, p, EXIT.USAGE, `usage: ${kind} import FILE --run ID`);
  try {
    const method = `${kind}.import` as const;
    const res = await withEngine(ctx, home, (e) => e.request(method, { run_id: runId, path: file })) as { imported: number; evidence_ids: string[] };
    emit(ctx, p, { ok: true, ...res }, [`${kind} imported ${res.imported} exchanges: ${res.evidence_ids.join(", ")}`]);
    return EXIT.OK;
  } catch (err) {
    return fail(ctx, p, EXIT.FAIL, (err as Error).message);
  }
}

// -- scope -----------------------------------------------------------------

async function cmdProgramImport(ctx: CliContext, p: Parsed, home: string): Promise<number> {
  ensureHome(home);
  const file = p.positionals[0] ?? str(p, "file");
  if (!file) return fail(ctx, p, EXIT.USAGE, "usage: program import POLICY_FILE [--run ID]");
  const runId = str(p, "run");
  try {
    const params: Record<string, unknown> = { file };
    if (runId) params.run_id = runId;
    const res = await withEngine(ctx, home, (e) => e.request("policy.import", params)) as {
      policy: { program_name: string; mode: string; include: number;
                exclude: number; unresolved: string[] };
    };
    const pol = res.policy;
    emit(ctx, p, { ok: true, ...res }, [
      `policy: ${pol.program_name} [${pol.mode}] +${pol.include}/-${pol.exclude}`,
      ...(pol.unresolved.length > 0
        ? ["unresolved:", ...pol.unresolved.map((u) => `  - ${u}`)]
        : ["no unresolved questions"]),
      ...(runId ? [`attached to ${runId}`] : ["(preview only — attach with --run ID)"]),
    ]);
    return EXIT.OK;
  } catch (err) {
    return fail(ctx, p, EXIT.FAIL, (err as Error).message);
  }
}

async function cmdScope(
  ctx: CliContext, p: Parsed, home: string, sub: string | undefined,
): Promise<number> {
  ensureHome(home);
  if (sub === "show") {
    const runId = str(p, "run") ?? p.positionals[0];
    if (!runId) return fail(ctx, p, EXIT.USAGE, "scope show --run ID");
    try {
      const res = await withEngine(ctx, home, (e) =>
        e.request("scope.show", { run_id: runId }),
      ) as Record<string, unknown>;
      const inc = (res.included_assets ?? []) as string[];
      const exc = (res.exclusions ?? []) as string[];
      const unres = (res.unresolved ?? []) as string[];
      emit(ctx, p, { ok: true, ...res }, [
        `${res.program_name} [${String(res.mode).toUpperCase()}] ${res.policy_source || "(no policy source)"}`,
        `included (${inc.length}): ${inc.join(", ") || "(unknown — never unrestricted)"}`,
        `excluded (${exc.length}): ${exc.join(", ") || "(none)"}`,
        `methods: ${(res.methods_allow as string[]).join(",") || "-"} deny: ${(res.methods_deny as string[]).join(",") || "-"}`,
        ...(unres.length > 0 ? ["unresolved:", ...unres.map((u) => `  - ${u}`)] : []),
      ]);
      return EXIT.OK;
    } catch (err) {
      return fail(ctx, p, EXIT.FAIL, engineError(err, runId));
    }
  }
  if (sub === "validate") return cmdScopeValidate(ctx, p, home);
  return fail(ctx, p, EXIT.USAGE, `unknown scope subcommand '${sub ?? ""}' (show|validate)`);
}

async function cmdScopeValidate(ctx: CliContext, p: Parsed, home: string): Promise<number> {
  const target = p.positionals[0];
  if (!target) return fail(ctx, p, EXIT.USAGE, "scope validate HOST_OR_URL [--run ID]");
  const runId = str(p, "run");
  if (runId) {
    // Deterministic dry-run against the run's loaded policy (static DNS only —
    // real resolution happens exclusively in the Phase-7 executor).
    try {
      const res = await withEngine(ctx, home, (e) =>
        e.request("policy.check", {
          run_id: runId,
          action: { kind: "http.request", method: "GET", url: target, impact: "R3" },
        }),
      ) as { allowed: boolean; reasons: string[] };
      emit(ctx, p, { ok: true, input: target, ...res }, [
        `input: ${target}`,
        `verdict: ${res.allowed ? "ALLOW (dry-run; execution needs Phase-7 gate + approval)" : "DENY"}`,
        ...res.reasons.map((r) => `  - ${r}`),
      ]);
      return EXIT.OK;
    } catch (err) {
      return fail(ctx, p, EXIT.FAIL, engineError(err, runId));
    }
  }
  const v = validateScope(target);
  emit(ctx, p, { ok: true, ...v }, [
    `input: ${v.input}`,
    `canonical: ${v.canonical ?? "(unparseable)"}`,
    "authorized: NO — scope unknown without a loaded policy (unknown is never unrestricted)",
    ...v.reasons.map((r) => `  - ${r}`),
  ]);
  return EXIT.OK;
}

// -- provider / models (Phase 3) ----------------------------------------------

interface ProviderInfo {
  name: string;
  builtin: boolean;
  type?: string;
  adapter?: string;
  endpoint_class?: string;
  transport?: string;
  base_url?: string;
  credential?: string;
}

async function cmdProvider(
  ctx: CliContext, p: Parsed, home: string, sub: string | undefined,
): Promise<number> {
  ensureHome(home);
  if (sub === "add") {
    const name = str(p, "name") ?? p.positionals[0];
    if (!name) return fail(ctx, p, EXIT.USAGE, "provider add --name NAME --type TYPE [--base-url U] [--credential env:VAR]");
    const cfg = {
      type: (str(p, "type") ?? "") as ProviderType,
      base_url: str(p, "base-url"),
      credential: str(p, "credential"),
    };
    const errs = validateProviderConfig(cfg);
    if (errs.length > 0) return fail(ctx, p, EXIT.USAGE, errs.join("; "));
    const res = writeProvider(home, name, cfg as Parameters<typeof writeProvider>[2], p.flags.get("force") === true);
    if (!res.ok) return fail(ctx, p, EXIT.USAGE, res.error ?? "write failed");
    if (cfg.credential?.startsWith("env:") && !(cfg.credential.slice(4) in ctx.env)) {
      ctx.err(`warning: ${cfg.credential} is not set in this environment (saved anyway)`);
    }
    emit(ctx, p, { ok: true, name, ...cfg }, [
      `provider '${name}' saved (${cfg.type}) — reference only, no secret stored`,
    ]);
    return EXIT.OK;
  }
  if (sub === "test") {
    const name = p.positionals[0];
    if (!name) return fail(ctx, p, EXIT.USAGE, "provider test PROVIDER [--model M] [--no-live]");
    return await cmdProviderTest(ctx, p, home, name);
  }
  return fail(ctx, p, EXIT.USAGE, `unknown provider subcommand '${sub ?? ""}' (add|test)`);
}

function credentialPresence(ctx: CliContext, ref: string | undefined): string {
  if (!ref) return "n/a (unconfigured)";
  const kind = ref.split(":")[0];
  if (kind === "env") {
    const v = ref.slice(4);
    return v in ctx.env ? "env:VAR set" : "env:VAR MISSING";
  }
  return `${kind}: present-check deferred to engine`;
}

async function cmdProviderTest(
  ctx: CliContext, p: Parsed, home: string, name: string,
): Promise<number> {
  const noLive = p.flags.get("no-live") === true;
  const report: Record<string, unknown> = { provider: name };
  const check = await withEngine(ctx, home, async (e) => {
    const list = (await e.request("provider.list")) as { providers: ProviderInfo[] };
    const info = list.providers.find((pr) => pr.name === name);
    if (!info) return { unknown: true as const };
    const models = (await e.request("model.list", { provider: name })) as {
      models: string[]; note?: string;
    };
    const model = str(p, "model") ?? models.models[0] ?? "default";
    const declared = (await e.request("model.probe", { provider: name, model })) as Record<string, unknown>;
    return { info, models: models.models, model, declared };
  });
  if ((check as { unknown?: boolean }).unknown) {
    return fail(ctx, p, EXIT.FAIL, `unknown provider '${name}' (see: provider list via models list)`);
  }
  const { info, models, model, declared } = check as {
    info: ProviderInfo; models: string[]; model: string; declared: Record<string, unknown>;
  };
  const credRef = (info.builtin ? undefined : info.credential) as string | undefined;
  const credStatus = credentialPresence(ctx, credRef);
  report.transport = info.transport;
  report.model = model;
  report.capabilities = (declared as { capabilities: unknown }).capabilities;
  report.credential = credStatus;
  report.reachability = info.transport === "local" ? "local (no network)" : "not checked";
  const lines = [
    `${name} [${info.transport}] model=${model}`,
    `credential: ${credStatus}`,
    `capabilities: ${Object.entries(((declared as { capabilities: Record<string, unknown> }).capabilities) ?? {})
      .filter(([, v]) => v === true).map(([k]) => k).join(" ") || "-"}`,
  ];
  if (credStatus.includes("MISSING")) {
    emit(ctx, p, { ...report, ok: false }, [...lines, "FAIL: credential missing"]);
    return EXIT.PROVIDER;
  }
  if (!noLive && info.transport !== "local") {
    try {
      const live = await withEngine(ctx, home, (e) =>
        e.request("model.probe", { provider: name, model, live: true }, 30000),
      ) as { verified_live?: boolean; reachability?: string; error?: unknown };
      report.reachability = live.reachability ?? (live.verified_live ? "reachable (verified)" : "unreachable");
      report.verified_live = live.verified_live ?? false;
      lines.push(`reachability: ${report.reachability}`);
      if (!live.verified_live) {
        emit(ctx, p, { ...report, ok: false }, [...lines, "FAIL: endpoint not verified"]);
        return EXIT.PROVIDER;
      }
    } catch (err) {
      const code = (err as { code?: number }).code;
      if (code === 1003) {
        report.reachability = "runtime unavailable (httpx/anyio); run `uv sync`";
        emit(ctx, p, { ...report, ok: true, partial: true }, [...lines, `reachability: ${report.reachability}`]);
        return EXIT.PARTIAL;
      }
      report.reachability = (err as Error).message;
      emit(ctx, p, { ...report, ok: false }, [...lines, `FAIL: ${report.reachability}`]);
      return EXIT.PROVIDER;
    }
  }
  emit(ctx, p, { ...report, ok: true }, lines);
  return EXIT.OK;
}

async function cmdModels(
  ctx: CliContext, p: Parsed, home: string, sub: string | undefined,
): Promise<number> {
  ensureHome(home);
  if (sub === "list") {
    const only = str(p, "provider");
    const res = await withEngine(ctx, home, async (e) => {
      const list = (await e.request("provider.list")) as { providers: ProviderInfo[] };
      const names = list.providers.map((pr) => pr.name).filter((n) => !only || n === only);
      const out: { provider: string; models: string[]; note?: string }[] = [];
      for (const n of names) {
        const ml = (await e.request("model.list", { provider: n })) as {
          models: string[]; note?: string;
        };
        out.push({ provider: n, ...ml });
      }
      return out;
    });
    emit(ctx, p, { ok: true, providers: res }, res.length === 0
      ? [`unknown provider '${only}'`]
      : res.flatMap((r) => [
          `${r.provider}: ${r.models.length > 0 ? r.models.join(", ") : "(set model explicitly)"}`,
          ...(r.note ? [`  note: ${r.note}`] : []),
        ]));
    return res.length === 0 ? EXIT.FAIL : EXIT.OK;
  }
  if (sub === "inspect") {
    const target = p.positionals[0];
    if (!target) return fail(ctx, p, EXIT.USAGE, "models inspect PROVIDER:MODEL [--live]");
    const [provider, model] = target.includes(":") ? target.split(":", 2) : [undefined, target];
    const res = await withEngine(ctx, home, async (e) => {
      let pname = provider;
      if (!pname) {
        const list = (await e.request("provider.list")) as { providers: ProviderInfo[] };
        const hit = list.providers.find((pr) => pr.name === "deepseek");
        void hit;
        // Search builtins with a matching declared model.
        for (const pr of list.providers) {
          if (!pr.builtin) continue;
          const ml = (await e.request("model.list", { provider: pr.name })) as { models: string[] };
          if (ml.models.includes(model)) {
            pname = pr.name;
            break;
          }
        }
        if (!pname) return { unknown: true as const };
      }
      try {
        const probe = (await e.request("model.probe",
          { provider: pname, model, live: p.flags.get("live") === true })) as Record<string, unknown>;
        return { probe };
      } catch (err) {
        if ((err as { code?: number }).code === -32602) return { unknown: true as const };
        throw err;
      }
    });
    if ((res as { unknown?: boolean }).unknown) {
      return fail(ctx, p, EXIT.FAIL, `unknown model '${target}' (try PROVIDER:MODEL)`);
    }
    const probe = (res as { probe: Record<string, unknown> }).probe;
    const caps = probe.capabilities as Record<string, unknown>;
    emit(ctx, p, { ok: true, ...probe }, [
      `${probe.provider}:${probe.model} [${probe.endpoint_class}] via ${probe.adapter}@${probe.adapter_version}`,
      `transport: ${probe.transport}${probe.verified_live ? " (verified live)" : " (declared only)"}`,
      `caps: ${Object.entries(caps).filter(([, v]) => v === true).map(([k]) => k).join(" ") || "-"}`,
      `strict: ${caps.strict_structured_output ? "native" : caps.structured_via_tool ? "via forced tool (recorded)" : "unsupported"}`,
    ]);
    return EXIT.OK;
  }
  return fail(ctx, p, EXIT.USAGE, `unknown models subcommand '${sub ?? ""}' (list|inspect)`);
}
