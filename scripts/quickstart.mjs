#!/usr/bin/env node
// One-command setup + launch — deepseek-harness / claude-code style.
// Usage from a fresh clone:  pnpm quickstart
//                            pnpm quickstart --lab tenancy-demo
//                            pnpm quickstart --plain
import { spawnSync } from "node:child_process";
import { existsSync } from "node:fs";

function run(cmd, args, opts = {}) {
  const r = spawnSync(cmd, args, { stdio: "inherit", ...opts });
  if (r.status !== 0) process.exit(r.status ?? 1);
}

const args = process.argv.slice(2);
const useLab = args.includes("--lab") ? args[args.indexOf("--lab") + 1] : null;
const plain = args.includes("--plain");

// 1. JS deps (skip if already present)
if (!existsSync("node_modules/.pnpm")) {
  console.log("▸ pnpm install --frozen-lockfile");
  run("pnpm", ["install", "--frozen-lockfile"]);
} else console.log("✓ node_modules present — skipping pnpm install");

// 2. Python env
if (!existsSync(".venv")) {
  console.log("▸ uv sync --python 3.12 --all-extras --all-packages");
  run("uv", ["sync", "--python", "3.12", "--all-extras", "--all-packages"]);
} else console.log("✓ .venv present — skipping uv sync");

// 3. Protocol (always — cheap, drift-gated)
console.log("▸ pnpm generate:protocol");
run("pnpm", ["generate:protocol"]);

// 4. Ensure a run exists, then launch TUI
import { execSync } from "node:child_process";
let runId = null;
try {
  const out = execSync("pnpm exec tsx apps/terminal/src/index.ts run list --json", { encoding: "utf8" });
  const j = JSON.parse(out);
  runId = j.runs?.[0]?.run_id ?? null;
} catch {}
if (!runId) {
  const createArgs = useLab
    ? ["exec", "tsx", "apps/terminal/src/index.ts", "run", "start", "--lab", useLab]
    : ["exec", "tsx", "apps/terminal/src/index.ts", "run", "start", "--mode", "plan"];
  console.log(`▸ pnpm ${createArgs.join(" ")}`);
  const out = execSync(`pnpm ${createArgs.join(" ")} --json`, { encoding: "utf8" });
  runId = JSON.parse(out).run_id;
}
console.log(`\n▸ launching TUI — run ${runId} ${plain ? "(plain)" : ""} — q to quit, / for palette, ? for help\n`);
const tuiArgs = ["exec", "tsx", "apps/terminal/src/index.ts", "tui", "--run", runId];
if (plain) tuiArgs.push("--plain");
run("pnpm", tuiArgs);
