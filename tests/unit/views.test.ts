import { describe, expect, it } from "vitest";
import type { RpcEvent } from "../../packages/protocol-ts/src/client.js";
import { resolveTheme } from "../../apps/terminal/src/themes.js";
import {
  frame,
  fuzzyMatch,
  renderApproval,
  renderConversation,
  renderPalette,
  renderQueue,
  renderRunSwitcher,
  renderStatusLine,
} from "../../apps/terminal/src/views.js";

const theme = resolveTheme("default", true); // noColor -> deterministic, no ANSI
const mono = resolveTheme("monochrome", false);

const ev = (method: string, params: object): RpcEvent =>
  ({ jsonrpc: "2.0", method, params, protocol_version: "0.1.0", trace_id: "t", ts: "" }) as RpcEvent;

describe("views (pure renderers)", () => {
  it("status line always shows the mode badge + budget", () => {
    const l = renderStatusLine(
      { mode: "artifacts", runId: "run_0001", model: "mock", budgetUsed: 3, budgetLimit: null, egress: "none" },
      100, theme,
    );
    expect(l).toContain("ARTIFACTS");
    expect(l).toContain("no target egress");
    expect(l).toContain("run_0001");
    expect(l).toContain("3/unknown");
  });

  it("run switcher + queue label statuses as text", () => {
    const runs = renderRunSwitcher(
      [{ run_id: "run_0001", mode: "plan", status: "paused", events: 5 }], 0, theme,
    );
    expect(runs.join("\n")).toContain("run_0001");
    expect(runs.join("\n")).toContain("paused");
    const q = renderQueue(
      [{ case_id: "C-0001", check_id: "T02.1", asset: "a", hypothesis: "member B exports A project",
         expected_rule: "r", baseline: "b", changed_variable: "v", observable_result: "o",
         stopping_point: "s", permission_ref: "P", result: "lead" }], 0, mono,
    );
    expect(q.join("\n")).toContain("LEAD (not a finding)");
    expect(q.join("\n")).toContain(">");
  });

  it("conversation redacts secrets from the secret fixture", async () => {
    const { readFileSync } = await import("node:fs");
    const seeds = readFileSync("tests/fixtures/secrets.txt", "utf8").split("\n").filter(Boolean);
    const lines = renderConversation(
      [ev("token.delta", { seq: 1, text: `saw key ${seeds[0]} in output` })], theme,
    ).join("\n");
    expect(lines).not.toContain(seeds[0]);
    expect(lines).toContain("[redacted]");
  });

  it("approval modal shows exact tool/destination/method/permission + a/d", () => {
    const lines = renderApproval(
      { tool: "http.request", destination: "https://authorized.example/x", method: "GET",
        requests: 1, sideEffects: "none", permissionRef: "P-1" }, 100, theme,
    ).join("\n");
    for (const s of ["http.request", "https://authorized.example/x", "GET", "P-1", "[a]pprove", "[d]eny"]) {
      expect(lines).toContain(s);
    }
  });

  it("palette fuzzy-matches; frame compacts under 80 cols", () => {
    expect(fuzzyMatch("rl", "run list")).toBe(true);
    expect(fuzzyMatch("zx", "run list")).toBe(false);
    expect(renderPalette(["run list", "case new"], "rn", theme).join("\n")).toContain("run list");
    const status = { mode: "plan", runId: null, model: "m", budgetUsed: 0, budgetLimit: null, egress: "none" as const };
    const narrow = frame("t", ["left"], ["right"], status, 60, theme).join("\n");
    expect(narrow).toContain("compact layout");
    const wide = frame("t", ["left"], ["right"], status, 100, theme).join("\n");
    expect(wide).toContain("|");
  });
});
