import { describe, expect, it } from "vitest";
import { mkdtempSync, writeFileSync } from "node:fs";
import { join } from "node:path";
import { tmpdir } from "node:os";
import { runCli, type CliContext } from "../../apps/terminal/src/commands.js";
import { EXIT } from "../../apps/terminal/src/exit-codes.js";
import { resolveTheme } from "../../apps/terminal/src/themes.js";
import { applyAction, createState } from "../../apps/terminal/src/tui.js";
import { renderScope } from "../../apps/terminal/src/views.js";
import { FakeEngine } from "../helpers/fake-engine.js";

const tmpHome = () => join(mkdtempSync(join(tmpdir(), "sf-pol-")), ".scopeforge");

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

describe("policy CLI (fake engine)", () => {
  it("program import previews; scope show states unknown; validate denies live", async () => {
    const home = tmpHome();
    const c = ctx(home);
    const run = (a: string[]) => runCli(["--home", home, ...a], c);
    expect(await run(["run", "start", "--mode", "plan"])).toBe(EXIT.OK);
    const policy = join(home, "policy.toml");
    writeFileSync(policy, '[policy]\nmode = "plan"\n');
    expect(await run(["program", "import", policy])).toBe(EXIT.OK);
    expect(c.outLines.join("\n")).toContain("preview only");
    expect(await run(["scope", "show", "--run", "run_0001"])).toBe(EXIT.OK);
    expect(c.outLines.join("\n")).toContain("UNKNOWN");
    expect(await run(["scope", "validate", "https://authorized.example/x", "--run", "run_0001"])).toBe(EXIT.OK);
    expect(c.outLines.join("\n")).toContain("DENY");
    expect(await run(["scope", "validate", "notaurl"])).toBe(EXIT.OK);
    expect(await run(["program", "import", "https://example.com/p.toml"])).toBe(EXIT.FAIL);
  });

  it("scope overlay routes + renders unresolved", () => {
    const theme = resolveTheme("default", true);
    const lines = renderScope({
      program_name: "P", mode: "plan", included_assets: [],
      unresolved: ["no included assets"],
    }, theme).join("\n");
    expect(lines).toContain("UNKNOWN");
    expect(lines).toContain("no included assets");
    const s = createState("/tmp");
    s.palette = "scope show";
    expect(applyAction(s, "select").outcome.notice).toBe("scope");
  });
});
