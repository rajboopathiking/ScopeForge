import { describe, expect, it } from "vitest";
import { mkdtempSync, writeFileSync } from "node:fs";
import { join } from "node:path";
import { tmpdir } from "node:os";
import { ensureHome, homeDir, readConfig } from "../../apps/terminal/src/home.js";
import {
  appendCase,
  listCases,
  nextCaseId,
  setCaseResult,
  showCase,
  validateCase,
} from "../../apps/terminal/src/case-store.js";
import { journalAppend, journalReplay, mergeEvents } from "../../apps/terminal/src/journal.js";
import { actionFor, DEFAULT_KEYS, keyName, loadKeymap } from "../../apps/terminal/src/keys.js";
import { canonicalizeTarget, validateScope } from "../../apps/terminal/src/scope.js";
import { resolveTheme, stripAnsi, sym } from "../../apps/terminal/src/themes.js";

const tmp = () => mkdtempSync(join(tmpdir(), "sf-"));

describe("home + config", () => {
  it("resolves SCOPEFORGE_HOME, scaffolds, reads defaults", () => {
    const home = join(tmp(), ".scopeforge");
    expect(homeDir("/x", { SCOPEFORGE_HOME: home })).toBe(home);
    expect(homeDir("/x", {})).toBe("/x/.scopeforge");
    ensureHome(home);
    expect(readConfig(home).theme).toBe("default");
  });
});

describe("case store", () => {
  const valid = {
    check_id: "T02.1", asset: "https://authorized.example", hypothesis: "h",
    expected_rule: "r", baseline: "b", changed_variable: "one var",
    observable_result: "o", stopping_point: "s", permission_ref: "P-1",
  };
  it("rejects empty/invalid cases with reasons", () => {
    expect(validateCase({})).toContain("missing check_id (e.g. T02.1)");
    expect(validateCase({ ...valid, check_id: "X99" })).toContain("check_id must match T01.1–T12.n");
  });
  it("appends, numbers, lists, shows, retries", () => {
    const home = tmp();
    expect(nextCaseId(home, "run_0001")).toBe("C-0001");
    const bad = appendCase(home, "run_0001", { ...valid, hypothesis: "" });
    expect(bad.errors?.length).toBeGreaterThan(0);
    const { record } = appendCase(home, "run_0001", valid);
    expect(record?.case_id).toBe("C-0001");
    expect(listCases(home, "run_0001")).toHaveLength(1);
    expect(showCase(home, "run_0001", "C-0001")?.result).toBe("queued");
    expect(setCaseResult(home, "run_0001", "C-0001", "lead")).toBe(true);
    expect(listCases(home, "run_0001", "lead")).toHaveLength(1);
    expect(nextCaseId(home, "run_0001")).toBe("C-0002");
  });
});

describe("journal", () => {
  const ev = (seq: number) => ({
    jsonrpc: "2.0" as const, method: "token.delta",
    params: { seq, text: "tok" }, protocol_version: "0.1.0",
    trace_id: "t", ts: "2026-09-16T00:00:00Z",
  });
  it("replays in order with maxSeq; merges dedupe by seq", () => {
    const home = tmp();
    expect(journalReplay(home, "run_1").events).toEqual([]);
    journalAppend(home, "run_1", ev(1));
    journalAppend(home, "run_1", ev(2));
    const r = journalReplay(home, "run_1");
    expect(r.maxSeq).toBe(2);
    expect(mergeEvents(r.events, [ev(2), ev(3)])).toHaveLength(3);
  });
});

describe("keys", () => {
  it("defaults complete; overlay overrides; garbage ignored", () => {
    expect(Object.keys(DEFAULT_KEYS)).toContain("approve");
    expect(keyName(Buffer.from("\u001b[A"))).toBe("up");
    expect(keyName(Buffer.from("\r"))).toBe("enter");
    const home = tmp();
    ensureHome(home);
    expect(actionFor(loadKeymap(home), "q")).toBe("quit");
    writeFileSync(join(home, "keybindings.json"), JSON.stringify({ quit: ["X"], bogus: ["y"] }));
    const map = loadKeymap(home);
    expect(actionFor(map, "X")).toBe("quit");
    expect(actionFor(map, "q")).toBeUndefined();
    writeFileSync(join(home, "keybindings.json"), "{corrupt");
    expect(actionFor(loadKeymap(home), "q")).toBe("quit");
  });
});

describe("scope stub", () => {
  it("canonicalizes and never authorizes", () => {
    expect(canonicalizeTarget("Example.COM./x").canonical).toBe("https://example.com/x");
    const v = validateScope("example.com");
    expect(v.authorized).toBe(false);
    expect(v.verdict).toBe("unknown");
    expect(validateScope("not a url at all !!!").canonical).toBeNull();
  });
});

describe("themes", () => {
  it("monochrome is ASCII + colorless; statuses are text, not color-only", () => {
    const mono = resolveTheme("monochrome", false);
    expect(mono.color).toBe(false);
    expect(sym(mono, "ok")).toBe("[ok]");
    expect(stripAnsi("\u001b[31mred\u001b[0m")).toBe("red");
    expect(resolveTheme("nope", false).name).toBe("default");
  });
});
