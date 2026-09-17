import { describe, expect, it } from "vitest";
import { mkdtempSync, readFileSync } from "node:fs";
import { join } from "node:path";
import { tmpdir } from "node:os";
import { runCli, type CliContext } from "../../apps/terminal/src/commands.js";
import { EXIT } from "../../apps/terminal/src/exit-codes.js";
import { readProviders } from "../../apps/terminal/src/providers-toml.js";
import { resolveTheme } from "../../apps/terminal/src/themes.js";
import { applyAction, createState } from "../../apps/terminal/src/tui.js";
import { renderModelInspector } from "../../apps/terminal/src/views.js";
import { FakeEngine } from "../helpers/fake-engine.js";

const tmpHome = () => join(mkdtempSync(join(tmpdir(), "sf-prov-")), ".scopeforge");

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

describe("provider / models CLI (fake engine)", () => {
  it("provider add validates, writes refs-only, refuses overwrite", async () => {
    const home = tmpHome();
    const c = ctx(home);
    const run = (a: string[]) => runCli(["--home", home, ...a], c);
    expect(await run(["provider", "add", "--name", "work", "--type", "deepseek",
      "--credential", "env:DEEPSEEK_API_KEY"])).toBe(EXIT.OK);
    expect(readProviders(home).work).toMatchObject({ type: "deepseek", credential: "env:DEEPSEEK_API_KEY" });
    const toml = readFileSync(join(home, "providers.toml"), "utf8");
    expect(toml).not.toMatch(/sk-|AKIA/);
    expect(await run(["provider", "add", "--name", "work", "--type", "mock"])).toBe(EXIT.USAGE);
    expect(await run(["provider", "add", "--name", "bad", "--type", "telepathy"])).toBe(EXIT.USAGE);
    expect(await run(["provider", "add", "--name", "leak", "--type", "mock",
      "--credential", "sk-live-0123456789abcdefghij"])).toBe(EXIT.USAGE);
    expect(readProviders(home).leak).toBeUndefined();
  });

  it("models list/inspect + provider test (mock OK, unknown FAIL)", async () => {
    const home = tmpHome();
    const c = ctx(home);
    const run = (a: string[]) => runCli(["--home", home, ...a], c);
    expect(await run(["models", "list"])).toBe(EXIT.OK);
    expect(c.outLines.join("\n")).toContain("deepseek-chat");
    expect(await run(["models", "inspect", "deepseek:deepseek-reasoner"])).toBe(EXIT.OK);
    expect(c.outLines.join("\n")).toContain("deepseek-reasoner");
    expect(await run(["models", "inspect", "deepseek-reasoner"])).toBe(EXIT.OK); // bare search
    expect(await run(["models", "inspect", "nope:m"])).toBe(EXIT.FAIL);
    expect(await run(["provider", "test", "mock"])).toBe(EXIT.OK);
    expect(await run(["provider", "test", "deepseek", "--no-live", "--json"])).toBe(EXIT.OK);
    expect(c.outLines.at(-1)).toContain('"credential":"n/a (unconfigured)"');
    expect(await run(["provider", "test", "nope"])).toBe(EXIT.FAIL);
    // Configured env credential missing -> PROVIDER failure, value never shown.
    expect(await run(["provider", "add", "--name", "w", "--type", "deepseek",
      "--credential", "env:SF_MISSING_KEY_XYZ"])).toBe(EXIT.OK);
    c.fake.extraProviders.push({ name: "w", credential: "env:SF_MISSING_KEY_XYZ" });
    expect(await run(["provider", "test", "w", "--no-live"])).toBe(EXIT.PROVIDER);
    expect(c.outLines.join("\n")).toContain("MISSING");
  });

  it("inspector view labels caps as text; palette routes to inspect", () => {
    const theme = resolveTheme("default", true);
    const lines = renderModelInspector([{
      provider: "deepseek", type: "deepseek", transport: "hosted",
      model: "deepseek-reasoner", verified: false,
      capabilities: { tool_calling: false, reasoning: true, strict_structured_output: true },
    }], theme).join("\n");
    expect(lines).toContain("deepseek-reasoner");
    expect(lines).toContain("no-tools");
    expect(lines).toContain("reasoning");
    expect(lines).toContain("declared only");
    const s = createState("/tmp");
    s.palette = "model inspect";
    const { outcome } = applyAction(s, "select");
    expect(outcome.notice).toBe("inspect");
  });
});
