import { spawn, type ChildProcess } from "node:child_process";
import { createInterface } from "node:readline";
import { randomUUID } from "node:crypto";
import {
  PROTOCOL_VERSION,
  assertCompatible,
  isKnownEvent,
  type RpcEvent,
  type RpcResponse,
} from "./generated.js";

export type { RpcEvent, RpcResponse };

export interface EngineEvents {
  onEvent?: (e: RpcEvent) => void;
  onLog?: (line: string) => void;
  /** Extra env for the engine process (e.g. SCOPEFORGE_HOME). */
  env?: Record<string, string>;
}

export interface Pending {
  resolve: (v: unknown) => void;
  reject: (e: Error) => void;
  timer: NodeJS.Timeout;
}

/** Minimal JSON-RPC 2.0 NDJSON client: ordered-seq tracking, restart/replay, fail-closed versions. */
export class EngineClient {
  private proc: ChildProcess | null = null;
  private nextId = 1;
  private pending = new Map<number | string, Pending>();
  private buffer = "";
  lastSeenSeq = new Map<string, number>();
  events: RpcEvent[] = [];
  onEvent: ((e: RpcEvent) => void) | undefined;
  onLog: ((line: string) => void) | undefined;

  constructor(
    private cmd: string = "python3",
    private args: string[] = [],
    opts: EngineEvents = {},
  ) {
    this.onEvent = opts.onEvent;
    this.onLog = opts.onLog;
    this.extraEnv = opts.env ?? {};
  }

  private extraEnv: Record<string, string>;

  start(): void {
    const proc = spawn(this.cmd, this.args, {
      stdio: ["pipe", "pipe", "pipe"],
      env: { ...process.env, ...this.extraEnv },
    });
    this.proc = proc;
    const rl = createInterface({ input: proc.stdout! });
    rl.on("line", (line) => this.ingest(line));
    proc.stderr!.on("data", (d) => this.onLog?.(String(d)));
    proc.on("exit", () => {
      // Only fail pending requests if the CURRENT engine died; a previous
      // generation exiting after restart() must not touch new requests.
      if (this.proc !== proc) return;
      for (const [, p] of this.pending) {
        clearTimeout(p.timer);
        p.reject(new Error("engine exited"));
      }
      this.pending.clear();
    });
  }

  private ingest(line: string): void {
    if (!line.trim()) return;
    let msg: RpcResponse & RpcEvent & { method?: string; id?: number | string };
    try {
      msg = JSON.parse(line);
    } catch {
      return;
    }
    if (msg.method !== undefined && msg.id === undefined) {
      const e = msg as RpcEvent;
      const seq = (e.params as { seq?: number } | undefined)?.seq;
      const run = (e as { run_id?: string }).run_id ?? "*";
      if (typeof seq === "number") {
        const prev = this.lastSeenSeq.get(run) ?? 0;
        if (seq > prev) this.lastSeenSeq.set(run, seq);
      }
      if (e.method && !isKnownEvent(String(e.method))) {
        this.onLog?.(`warn: unknown event ${String(e.method)} ignored\n`);
        return;
      }
      this.events.push(e);
      this.onEvent?.(e);
      return;
    }
    const id = msg.id!;
    const p = this.pending.get(id);
    if (!p) return;
    this.pending.delete(id);
    clearTimeout(p.timer);
    if (msg.error) {
      const err = new Error(`RPC ${JSON.stringify(msg.error)}`) as Error & {
        code?: number;
      };
      err.code = (msg.error as { code?: number }).code;
      p.reject(err);
    } else {
      p.resolve(msg.result);
    }
  }

  request(method: string, params: Record<string, unknown> = {}, timeoutMs = 15000): Promise<unknown> {
    const id = this.nextId++;
    const envelope = {
      jsonrpc: "2.0",
      id,
      method,
      params,
      protocol_version: PROTOCOL_VERSION,
      trace_id: randomUUID().slice(0, 12),
      ts: new Date().toISOString(),
      ...(params.run_id ? { run_id: params.run_id } : {}),
    };
    return new Promise((resolve, reject) => {
      const timer = setTimeout(() => {
        this.pending.delete(id);
        reject(new Error(`request timeout: ${method}`));
      }, timeoutMs);
      this.pending.set(id, { resolve: resolve as (v: unknown) => void, reject, timer });
      this.proc!.stdin!.write(JSON.stringify(envelope) + "\n");
    });
  }

  notify(method: string, params: Record<string, unknown> = {}): void {
    this.proc!.stdin!.write(
      JSON.stringify({
        jsonrpc: "2.0",
        method,
        params,
        protocol_version: PROTOCOL_VERSION,
        trace_id: randomUUID().slice(0, 12),
        ts: new Date().toISOString(),
      }) + "\n",
    );
  }

  async initialize(client = "terminal/0.1.0"): Promise<{ protocol_version: string } & Record<string, unknown>> {
    const res = (await this.request("initialize", {
      protocol_version: PROTOCOL_VERSION,
      client,
    })) as { protocol_version: string };
    assertCompatible(res.protocol_version);
    return res;
  }

  /** Restart engine and re-handshake; caller replays from lastSeenSeq (dedup by seq). */
  restart(): Map<string, number> {
    const seen = new Map(this.lastSeenSeq);
    this.proc?.kill();
    this.events = [];
    this.start();
    return seen;
  }

  /** Send a signal to the engine (SIGKILL in crash-resume tests). */
  terminate(signal: NodeJS.Signals = "SIGTERM"): void {
    try {
      this.proc?.kill(signal);
    } catch {
      /* already gone */
    }
  }

  async stop(): Promise<void> {
    try {
      await this.request("shutdown", {}, 3000);
    } catch {
      /* best effort */
    }
    this.proc?.kill();
    this.proc = null;
  }

  get stdoutBuffer(): string {
    return this.buffer;
  }
}
