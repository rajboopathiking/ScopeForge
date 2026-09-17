import { describe, expect, it } from "vitest";
import { mkdtempSync, writeFileSync } from "node:fs";
import { join } from "node:path";
import { tmpdir } from "node:os";
import { runCli, type CliContext } from "../../apps/terminal/src/commands.js";
import { EXIT } from "../../apps/terminal/src/exit-codes.js";
import { applyAction, createState, renderState } from "../../apps/terminal/src/tui.js";
import { resolveTheme } from "../../apps/terminal/src/themes.js";
import { FakeEngine } from "../helpers/fake-engine.js";

const tmpHome = () => join(mkdtempSync(join(tmpdir(), "sf-rec-")), ".scopeforge");

function ctx(home: string): CliContext & { outLines: string[]; errLines: string[]; fake: FakeEngine } {
  const outLines: string[] = [];
  const errLines: string[] = [];
  const fake = new FakeEngine();
  return {
    cwd: tmpdir(), env: {}, isTTY: false,
    out: (s) => outLines.push(s), err: (s) => errLines.push(s),
    engine: async () => fake,
    outLines, errLines, fake,
  };
}

describe("records CLI (fake engine)", () => {
  it("artifact import -> evidence show/redact", async () => {
    const home = tmpHome();
    const c = ctx(home);
    const run = (a: string[]) => runCli(["--home", home, ...a], c);
    expect(await run(["run", "start", "--mode", "artifacts"])).toBe(EXIT.OK);
    const file = join(home, "in.txt");
    writeFileSync(file, "hello");
    expect(await run(["artifact", "import", file, "--run", "run_0001"])).toBe(EXIT.OK);
    expect(c.outLines.join("\n")).toContain("E-001");
    expect(await run(["artifact", "import", "--run", "run_0001"])).toBe(EXIT.USAGE);
    expect(await run(["evidence", "show", "--run", "run_0001"])).toBe(EXIT.OK);
    expect(await run(["evidence", "show", "E-001", "--run", "run_0001", "--preview"])).toBe(EXIT.OK);
    expect(await run(["evidence", "redact", "E-001", "--run", "run_0001"])).toBe(EXIT.OK);
    expect(c.outLines.join("\n")).toContain("E-002");
    expect(await run(["evidence", "show", "E-999", "--run", "run_0001"])).toBe(EXIT.FAIL);
  });

  it("report build needs a complete finding file; export prints warnings", async () => {
    const home = tmpHome();
    const c = ctx(home);
    const run = (a: string[]) => runCli(["--home", home, ...a], c);
    expect(await run(["run", "start", "--mode", "plan"])).toBe(EXIT.OK);
    const thin = join(home, "thin.json");
    writeFileSync(thin, JSON.stringify({ title: "t" }));
    expect(await run(["report", "build", "--run", "run_0001", "--finding-file", thin])).toBe(EXIT.USAGE);
    const full = join(home, "full.json");
    writeFileSync(full, JSON.stringify({ title: "t", evidence: ["E-001"] }));
    expect(await run(["report", "build", "--run", "run_0001", "--finding-file", full])).toBe(EXIT.OK);
    expect(await run(["export", "run_0001"])).toBe(EXIT.OK);
    expect(c.outLines.join("\n")).toContain("export.tar.gz");
  });

  it("TUI evidence overlay routes + renders", () => {
    const theme = resolveTheme("default", true);
    const s = createState("/tmp");
    s.palette = "evidence list";
    const { outcome } = applyAction(s, "select");
    expect(outcome.notice).toBe("evidence");
    s.overlay = { title: "Evidence (1)", lines: ["  E-001 [note/original] hello"] };
    const screen = renderState(s, theme, 100).join("\n");
    expect(screen).toContain("Evidence (1)");
    expect(screen).toContain("esc/enter closes");
  });
});
