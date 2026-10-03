import { describe, expect, it } from "vitest";
import { EngineClient } from "../../packages/protocol-ts/src/client.js";
import {
  METHOD_NAMES,
  PROTOCOL_VERSION,
} from "../../packages/protocol-ts/src/generated.js";
import { spawn } from "node:child_process";

const SERVER = new URL(
  "../../services/engine/src/scopeforge_engine/server.py",
  import.meta.url,
).pathname;

function rawCall(
  lines: string[],
  expectLines: number,
  timeoutMs = 8000,
): Promise<string[]> {
  return new Promise((resolve, reject) => {
    const p = spawn("python3", [SERVER]);
    let buf = "";
    const out: string[] = [];
    const timer = setTimeout(() => {
      p.kill();
      reject(new Error("timeout waiting engine output"));
    }, timeoutMs);
    p.stdout.on("data", (d: Buffer) => {
      buf += d.toString();
      let idx: number;
      while ((idx = buf.indexOf("\n")) >= 0) {
        out.push(buf.slice(0, idx));
        buf = buf.slice(idx + 1);
        if (out.length >= expectLines) {
          clearTimeout(timer);
          p.kill();
          resolve(out);
          return;
        }
      }
    });
    p.on("error", reject);
    for (const l of lines) p.stdin.write(l + "\n");
  });
}

describe("protocol spine (Phase 1 exit criteria)", () => {
  it("handshake exposes capabilities + health", async () => {
    const c = new EngineClient("python3", [SERVER]);
    c.start();
    try {
      const init = (await c.initialize()) as { protocol_version: string };
      expect(init.protocol_version).toBe(PROTOCOL_VERSION);
      const caps = (await c.request("capabilities")) as { methods: string[] };
      for (const m of ["initialize", "capabilities", "health", "shutdown", "run.create", "run.cancel"])
        expect(caps.methods).toContain(m);
      expect(METHOD_NAMES).toContain("initialize");
      const h = (await c.request("health")) as { ok: boolean };
      expect(h.ok).toBe(true);
    } finally {
      await c.stop();
    }
  });

  it("streams 10,000 ordered events without loss", async () => {
    const c = new EngineClient("python3", [SERVER]);
    const seqs: number[] = [];
    c.onEvent = (e) => {
      if (e.method === "token.delta") seqs.push((e.params as { seq: number }).seq);
    };
    c.start();
    try {
      await c.initialize();
      const res = (await c.request("event.subscribe", { count: 10000 }, 60000)) as {
        received: number;
      };
      expect(res.received).toBe(10000);
      expect(seqs).toHaveLength(10000);
      for (let i = 1; i < seqs.length; i++) expect(seqs[i]).toBeGreaterThan(seqs[i - 1]);
    } finally {
      await c.stop();
    }
  }, 90000);

  it("cancellation stops a synthetic long task within one second", async () => {
    const c = new EngineClient("python3", [SERVER]);
    let seen = 0;
    c.onEvent = (e) => {
      if (e.method === "token.delta") seen++;
    };
    c.start();
    try {
      await c.initialize();
      const sub = c.request("event.subscribe", { count: 20000 }, 60000) as Promise<{
        received: number;
        cancelled: boolean;
      }>;
      while (seen < 300) await new Promise((r) => setTimeout(r, 10));
      const t0 = Date.now();
      c.notify("tool.cancel", { tool_call_id: "*" });
      const res = await sub;
      expect(Date.now() - t0).toBeLessThan(1000);
      expect(res.cancelled).toBe(true);
      expect(res.received).toBeLessThan(20000);
    } finally {
      await c.stop();
    }
  }, 30000);

  it("rejects incompatible major protocol versions with actionable message", async () => {
    const lines = [
      JSON.stringify({
        jsonrpc: "2.0",
        id: 1,
        method: "initialize",
        params: { protocol_version: "99.0.0", client: "test" },
        protocol_version: "99.0.0",
        trace_id: "t-reject-1",
        ts: new Date().toISOString(),
      }),
    ];
    const out = await rawCall(lines, 1);
    const msg = JSON.parse(out[0]);
    expect(msg.error.code).toBe(1001);
    expect(String(msg.error.message)).toMatch(/major/i);
  });

  it("returns -32601 for unknown methods and survives restarts with replay", async () => {
    const c = new EngineClient("python3", [SERVER]);
    c.start();
    try {
      await c.initialize();
      await expect(c.request("nope.unknown", {})).rejects.toThrow(/-32601|Method not found/);
      await c.request("event.subscribe", { count: 50 }, 15000);
      const seen = new Map(c.lastSeenSeq);
      expect((seen.get("*") ?? 0) >= 50).toBe(true);
      c.restart();
      await c.initialize();
      const res = (await c.request("event.subscribe", { count: 20 }, 15000)) as {
        received: number;
      };
      expect(res.received).toBe(20);
    } finally {
      await c.stop();
    }
  }, 30000);
});
