import { describe, expect, it } from "vitest";
import { mkdtempSync } from "node:fs";
import { join } from "node:path";
import { tmpdir } from "node:os";
import { runCli, type CliContext } from "../../apps/terminal/src/commands.js";
import { EXIT } from "../../apps/terminal/src/exit-codes.js";
import { resolveTheme } from "../../apps/terminal/src/themes.js";
import { applyAction, createState, summarizeTurn } from "../../apps/terminal/src/tui.js";
import { renderCoach, renderConversation, renderCoverageSummary } from "../../apps/terminal/src/views.js";
import type { RpcEvent } from "../../packages/protocol-ts/src/client.js";
import { FakeEngine } from "../helpers/fake-engine.js";

const tmpHome = () => join(mkdtempSync(join(tmpdir(), "sf-agent-")), ".scopeforge");
const theme = resolveTheme("default", true);
const ev = (method: string, params: object): RpcEvent =>
  ({ jsonrpc: "2.0", method, params, protocol_version: "0.1.0", trace_id: "t", ts: "" }) as RpcEvent;

describe("agent UI (fake engine)", () => {
  it("conversation renders agent turns + local notes, redacted", () => {
    const lines = renderConversation([
      ev("chat.local", { text: "check T02" }),
      ev("agent.turn", { role: "validator", text: "verdict: lead (AKIAIOSFODNN7SEEDKE1?)" }),
    ], theme).join("\n");
    expect(lines).toContain("you: check T02");
    expect(lines).toContain("assistant [validator]: verdict: lead");
    expect(lines).not.toContain("AKIAIOSFODNN7SEEDKE1");
  });

  it("summarizeTurn compresses role outputs deterministically", () => {
    expect(summarizeTurn({ role: "validator", output: { verdict: "lead" }, gaps: [] }))
      .toBe("verdict: lead");
    expect(summarizeTurn({ role: "test_designer",
      output: { check_id: "T02.1", hypothesis: "h" }, gaps: [] })).toContain("T02.1");
    expect(summarizeTurn({ role: "x", output: null, gaps: ["empty"] })).toContain("no proposal");
  });

  it("palette routes coach + coverage; views render scores and statuses", () => {
    const s = createState("/tmp");
    s.palette = "coach";
    expect(applyAction(s, "select").outcome.notice).toBe("coach");
    s.palette = "coverage";
    expect(applyAction({ ...s }, "select").outcome.notice).toBe("coverage");
    const coach = renderCoach("C-0001",
      { hypothesis_quality: { score: 3, reasons: ["rule stated"] } }, theme).join("\n");
    expect(coach).toContain("hypothesis_quality: 3/4");
    const cov = renderCoverageSummary({ categories: [
      { id: "T02", checks: [{ status: "tested" }, { status: "not_executed" }] },
    ] }, theme).join("\n");
    expect(cov).toContain("T02:");
    expect(cov).toContain("not_executed");
  });

  it("engine agent methods answer through runCli-shaped calls", async () => {
    const home = tmpHome();
    const outLines: string[] = [];
    const fake = new FakeEngine();
    const c: CliContext = {
      cwd: tmpdir(), env: {}, isTTY: false,
      out: (s) => outLines.push(s), err: () => {},
      engine: async () => fake,
    };
    expect(await runCli(["--home", home, "run", "start", "--mode", "plan"], c)).toBe(EXIT.OK);
    const chat = await fake.request("chat.submit", { run_id: "run_0001", message: "hi" }) as { gaps: string[]; stored: boolean };
    expect(chat.gaps).toEqual([]);
    expect(chat.stored).toBe(true);
    const score = await fake.request("coach.score", { run_id: "run_0001", case_id: "C-0001" }) as { scores: object };
    expect(Object.keys(score.scores)).toHaveLength(10);
    const cov = await fake.request("coverage.report", { run_id: "run_0001" }) as { categories: unknown[] };
    expect(cov.categories).toHaveLength(12);
  });
});
