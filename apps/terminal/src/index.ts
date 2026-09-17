#!/usr/bin/env node
// scopeforge entry: real CLI (Phase 2). See src/commands.ts for the surface.
import { runCli } from "./commands.js";

const code = await runCli(process.argv.slice(2), {
  cwd: process.cwd(),
  env: process.env,
  isTTY: Boolean(process.stdout.isTTY),
  columns: process.stdout.columns,
  out: (s) => console.log(s),
  err: (s) => console.error(s),
});
process.exitCode = code;
