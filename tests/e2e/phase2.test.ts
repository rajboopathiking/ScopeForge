import { describe, expect, it } from "vitest";
import { mkdtempSync } from "node:fs";
import { join } from "node:path";
import { tmpdir } from "node:os";
import { EngineClient, type RpcEvent } from "../../packages/protocol-ts/src/client.js";
import { runCli, type CliContext } from "../../apps/terminal/src/commands.js";
import { journalAppend, journalReplay } from "../../apps/terminal/src/journal.js";

const SERVER = new URL(
  "../../services/engine/src/scopeforge_engine/server.py",
  import.meta.url,
).pathname;

const tmpHome = () => join(mkdtempSync(join(tmpdir(), "sf-e2e-")), ".scopeforge");
const seqOf = (e: RpcEvent) => (e.params as { seq?: number }).seq as number;

describe("Phase 2 gates over the real engine", () => {
  it("SIGKILL mid-stream loses no displayed events; resume continues the sequence", async () => {
    const home = tmpHome();
    const mk = () => new EngineClient("python3", [SERVER], { env: { SCOPEFORGE_HOME: home } });
    const c1 = mk();
    const seen: RpcEvent[] = [];
    c1.onEvent = (e) => {
      if (e.method === "token.delta") {
        seen.push(e);
        journalAppend(home, "run_0001", e);
      }
    };
    c1.start();
    await c1.initialize("terminal/0.1.0");
    const created = (await c1.request("run.create", { mode: "plan" })) as { run_id: string };
    expect(created.run_id).toBe("run_0001");
    const sub = c1.request("event.subscribe", { run_id: "run_0001", count: 4000 }, 60000);
    while (seen.length < 300) await new Promise((r) => setTimeout(r, 5));
    c1.terminate("SIGKILL"); // crash — no shutdown, no flush beyond periodic snapshots
    await expect(sub).rejects.toThrow();
    const before = seen.length;
    expect(before).toBeGreaterThanOrEqual(300);

    // Fresh engine process: registry reloaded, sequence continues.
    const c2 = mk();
    c2.start();
    try {
      await c2.initialize("terminal/0.1.0");
      const list = (await c2.request("run.list")) as {
        runs: { run_id: string; events: number }[];
      };
      expect(list.runs[0].run_id).toBe("run_0001");
      expect(list.runs[0].events).toBeGreaterThan(0);
      const after: RpcEvent[] = [];
      c2.onEvent = (e) => {
        if (e.method === "token.delta") {
          after.push(e);
          journalAppend(home, "run_0001", e);
        }
      };
      const lastSeen = Math.max(...seen.map(seqOf));
      const res = (await c2.request(
        "event.subscribe", { run_id: "run_0001", count: 500, from_seq: lastSeen }, 60000,
      )) as { received: number };
      expect(res.received).toBe(500);
      // No gaps across the crash: unique seqs are contiguous and progress was made.
      const uniq = [...new Set([...seen, ...after].map(seqOf))].sort((a, b) => a - b);
      for (let i = 1; i < uniq.length; i++) expect(uniq[i] - uniq[i - 1]).toBe(1);
      expect(uniq.at(-1)).toBeGreaterThan(lastSeen);
      // Journal holds every displayed event; CLI resume replays it.
      const replay = journalReplay(home, "run_0001");
      expect(replay.events.length).toBe(before + 500);
      const lines: string[] = [];
      const code = await runCli(["--home", home, "run", "resume", "--run", "run_0001"], {
        cwd: tmpdir(), env: {}, isTTY: false,
        out: (s) => lines.push(s), err: () => {},
      } as CliContext);
      expect(code).toBe(0);
      expect(lines.join("\n")).toContain(`replayed ${before + 500} journaled events`);
    } finally {
      await c2.stop();
    }
  }, 90000);

  it("doctor + run commands work against the real engine", async () => {
    const home = tmpHome();
    const lines: string[] = [];
    const errs: string[] = [];
    const base = {
      cwd: tmpdir(), env: {}, isTTY: false,
      out: (s: string) => lines.push(s), err: (s: string) => errs.push(s),
    } as CliContext;
    expect(await runCli(["--home", home, "doctor"], base)).toBe(0);
    expect(await runCli(["--home", home, "run", "start", "--mode", "artifacts", "--json"], base)).toBe(0);
    expect(lines.at(-1)).toContain("run_0001");
    expect(await runCli(["--home", home, "run", "status", "--run", "run_0001"], base)).toBe(0);
  }, 60000);
});
