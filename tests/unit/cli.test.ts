import { describe, expect, it } from "vitest";
import { mkdtempSync } from "node:fs";
import { join } from "node:path";
import { tmpdir } from "node:os";
import { runCli, type CliContext } from "../../apps/terminal/src/commands.js";
import { EXIT } from "../../apps/terminal/src/exit-codes.js";
import { FakeEngine } from "../helpers/fake-engine.js";

function ctx(home: string, extra: Partial<CliContext> = {}): CliContext & { outLines: string[]; errLines: string[] } {
  const outLines: string[] = [];
  const errLines: string[] = [];
  const fake = new FakeEngine();
  return {
    cwd: tmpdir(), env: {}, isTTY: false,
    out: (s) => outLines.push(s), err: (s) => errLines.push(s),
    engine: async () => fake,
    outLines, errLines, ...extra,
  };
}

const tmpHome = () => join(mkdtempSync(join(tmpdir(), "sf-cli-")), ".scopeforge");
const run = (argv: string[], c: CliContext) => runCli(argv, c);

describe("CLI surface (fake engine)", () => {
  it("full run lifecycle with --home", async () => {
    const home = tmpHome();
    const c = ctx(home);
    expect(await run(["--home", home, "init"], c)).toBe(EXIT.OK);
    expect(await run(["--home", home, "run", "start", "--mode", "plan", "--json"], c)).toBe(EXIT.OK);
    expect(c.outLines.at(-1)).toContain("run_0001");
    expect(await run(["--home", home, "run", "list"], c)).toBe(EXIT.OK);
    expect(await run(["--home", home, "run", "status", "--run", "run_0001"], c)).toBe(EXIT.OK);
    expect(await run(["--home", home, "run", "cancel", "--run", "run_0001"], c)).toBe(EXIT.OK);
    expect(await run(["--home", home, "run", "close", "--run", "run_0001", "--json"], c)).toBe(EXIT.OK);
    expect(await run(["--home", home, "run", "status", "--run", "run_9999"], c)).toBe(EXIT.FAIL);
  });

  it("live start is policy-blocked; bad mode is usage error", async () => {
    const home = tmpHome();
    const c = ctx(home);
    expect(await run(["--home", home, "run", "start", "--mode", "live"], c)).toBe(EXIT.POLICY_BLOCKED);
    expect(await run(["--home", home, "run", "start", "--mode", "yolo"], c)).toBe(EXIT.USAGE);
  });

  it("case new validates; list/show/retry work", async () => {
    const home = tmpHome();
    const c = ctx(home);
    expect(await run(["--home", home, "run", "start", "--mode", "plan"], c)).toBe(EXIT.OK);
    const base = ["--home", home, "case", "new", "--run", "run_0001", "--check", "T02.1",
      "--asset", "https://authorized.example", "--hypothesis", "h", "--expected-rule", "r",
      "--baseline", "b", "--changed", "one variable", "--observable", "o",
      "--stopping", "s", "--permission", "P-1"];
    expect(await run(["--home", home, "case", "new", "--run", "run_0001"], c)).toBe(EXIT.USAGE);
    expect(await run(base, c)).toBe(EXIT.OK);
    expect(await run(["--home", home, "case", "list", "--run", "run_0001"], c)).toBe(EXIT.OK);
    expect(c.outLines.at(-1)).toContain("C-0001");
    expect(await run(["--home", home, "case", "show", "C-0001", "--run", "run_0001"], c)).toBe(EXIT.OK);
    expect(await run(["--home", home, "case", "retry", "C-0001", "--run", "run_0001"], c)).toBe(EXIT.OK);
    expect(await run(["--home", home, "case", "show", "C-9999", "--run", "run_0001"], c)).toBe(EXIT.FAIL);
  });

  it("scope validate never authorizes; stubs name their phase; unknown errors", async () => {
    const c = ctx(tmpHome());
    expect(await run(["scope", "validate", "Example.COM./x", "--json"], c)).toBe(EXIT.OK);
    expect(c.outLines.at(-1)).toContain('"authorized":false');
    expect(await run(["tool", "list"], c)).toBe(EXIT.OK);
    expect(await run(["mcp", "list"], c)).toBe(EXIT.OK);
    expect(await run(["eval", "run", "suite"], c)).toBe(EXIT.NOT_IMPLEMENTED);
    expect(await run(["frobnicate"], c)).toBe(EXIT.USAGE);
  });

  it("tui refuses non-TTY without --plain/--once", async () => {
    const c = ctx(tmpHome());
    expect(await run(["tui"], c)).toBe(EXIT.USAGE);
  });

  it("tui --once streams, journals, and shows the approval modal", async () => {
    const home = tmpHome();
    const fake = new FakeEngine();
    const orig = fake.request.bind(fake);
    fake.request = async (m: string, p: Record<string, unknown> = {}) =>
      m === "event.subscribe" ? orig(m, { ...p, count: 5, demoApproval: true }) : orig(m, p);
    const engine = async () => fake;
    const c = ctx(home, { engine });
    expect(await run(["--home", home, "run", "start", "--mode", "plan"], c)).toBe(EXIT.OK);
    const code = await run(["--home", home, "tui", "--run", "run_0001", "--once"], c);
    expect(code).toBe(EXIT.OK);
    const screen = c.outLines.join("\n");
    expect(screen).toContain("Approval required");
    expect(screen).toContain("http.request");
    const { journalReplay } = await import("../../apps/terminal/src/journal.js");
    const r = journalReplay(home, "run_0001");
    expect(r.events.length).toBeGreaterThanOrEqual(6); // 5 deltas + approval
    // Second --once replays the journal (crash-resume of the displayed sequence).
    const c2 = ctx(home, { engine });
    await run(["--home", home, "tui", "--run", "run_0001", "--once"], c2);
    expect(c2.errLines.join("\n")).toContain("resumed");
  });
});
