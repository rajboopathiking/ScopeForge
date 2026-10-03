import { describe, expect, it } from "vitest";
import { mkdtempSync } from "node:fs";
import { join } from "node:path";
import { tmpdir } from "node:os";
import { runCli, type CliContext } from "../../apps/terminal/src/commands.js";
import { EXIT } from "../../apps/terminal/src/exit-codes.js";
import { resolveTheme } from "../../apps/terminal/src/themes.js";
import { applyAction, createState } from "../../apps/terminal/src/tui.js";
import { renderApproval, renderExecution } from "../../apps/terminal/src/views.js";
import { FakeEngine } from "../helpers/fake-engine.js";

const tmpHome = () => join(mkdtempSync(join(tmpdir(), "sf-live-")), ".scopeforge");
const theme = resolveTheme("default", true);

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

describe("live UI (fake engine)", () => {
  it("run start --lab scopes a live run to loopback", async () => {
    const home = tmpHome();
    const c = ctx(home);
    const code = await runCli(["--home", home, "run", "start", "--lab", "tenancy-demo"], c);
    expect(code).toBe(EXIT.OK);
    expect(c.outLines.join("\n")).toContain("loopback only");
    expect(await runCli(["--home", home, "run", "start", "--lab", "nope"], c)).toBe(EXIT.FAIL);
  });

  it("palette routes execute + ediff; execution renders all outcomes", () => {
    const s = createState("/tmp");
    s.palette = "case execute";
    expect(applyAction(s, "select").outcome.notice).toBe("execute");
    s.palette = "evidence diff";
    expect(applyAction({ ...s }, "select").outcome.notice).toBe("ediff");
    expect(renderExecution({ sent: true, status: 403, evidence_ids: ["E-1"] }, theme).join("\n"))
      .toContain("status: 403");
    expect(renderExecution({ sent: false, reasons: ["denied x"], approval_required: true }, theme).join("\n"))
      .toContain("not sent");
    expect(renderExecution({ transport_error: "timeout" }, theme).join("\n")).toContain("no partial state");
    const modal = renderApproval({
      tool: "http.request", destination: "d", method: "POST", requests: 1,
      sideEffects: "s", permissionRef: "P", scope: "http.request:POST:d",
    }, 100, theme).join("\n");
    expect(modal).toContain("http.request:POST:d");
  });

  it("fake live methods answer", async () => {
    const fake = new FakeEngine();
    expect(await fake.request("lab.list", {})).toHaveProperty("labs");
    const exec = await fake.request("tool.execute", { run_id: "r", action: {} }) as { status: number };
    expect(exec.status).toBe(200);
    const denied = await fake.request("experiment.compare", {
      run_id: "r", baseline: { a: 1 }, variant: { a: 1, b: 2 },
    }).catch((e: Error) => e);
    expect(String(denied)).toContain("one changed variable");
  });
});
