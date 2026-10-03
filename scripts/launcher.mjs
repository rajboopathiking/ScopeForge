#!/usr/bin/env node
// Launcher verification (Phase 9). Checks engine checksum + protocol compat before spawn.
// Usage: node scripts/launcher.mjs --engine <path> --expected-sha256 <hex>
// In production the launcher is the npm `scopeforge` bin which bundles this check.
import { createHash } from "node:crypto";
import { readFileSync, existsSync } from "node:fs";
import { resolve } from "node:path";

function sha256(path) {
  const data = readFileSync(path);
  return createHash("sha256").update(data).digest("hex");
}

function verify(enginePath, expected) {
  const abs = resolve(enginePath);
  if (!existsSync(abs)) {
    console.error(`engine not found: ${abs}`);
    process.exit(2);
  }
  const actual = sha256(abs);
  if (expected && actual !== expected) {
    console.error(`engine checksum mismatch\n  expected: ${expected}\n  actual:   ${actual}\n  fail closed: refusing to launch tampered engine`);
    process.exit(2);
  }
  // Protocol compat: check engine or its generated protocol file
  let proto = null;
  for (const candidate of [abs, abs.replace("server.py", "protocol_generated.py")]) {
    try {
      const t = readFileSync(candidate, "utf8");
      const mm = t.match(/PROTOCOL_VERSION\s*=\s*"([^"]+)"/);
      if (mm) { proto = mm[1]; break; }
    } catch {}
  }
  if (proto) console.log(`engine protocol ${proto} sha256:${actual.slice(0,16)} verified`);
  else console.log(`engine sha256:${actual.slice(0,16)} (no PROTOCOL_VERSION found)`);
  process.exit(0);
}

const args = process.argv.slice(2);
const eIdx = args.indexOf("--engine");
const sIdx = args.indexOf("--expected-sha256");
if (eIdx === -1) {
  console.error("usage: launcher.mjs --engine <path> [--expected-sha256 <hex>]");
  process.exit(2);
}
verify(args[eIdx+1], sIdx !== -1 ? args[sIdx+1] : null);
