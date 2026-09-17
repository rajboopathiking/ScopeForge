#!/usr/bin/env node
// Shim so `scopeforge tui` works like `claude` — no global install needed.
// From repo root:  ./scripts/scopeforge.js tui --run run_0001
// Or via pnpm:     pnpm scopeforge tui --run run_0001
// After `npm link`: scopeforge tui
import { spawn } from "node:child_process";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

const root = join(dirname(fileURLToPath(import.meta.url)), "..");
const entry = join(root, "apps/terminal/src/index.ts");
const args = process.argv.slice(2);
if (args.length === 0) args.push("--help");

// Use tsx via dlx (no build step) — same as quickstart
const child = spawn("pnpm", ["dlx", "-y", "tsx", entry, ...args], { stdio: "inherit", cwd: root });
child.on("exit", (c) => process.exit(c ?? 0));
