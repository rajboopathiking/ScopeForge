// Launcher verification (Phase 9) — TS side used by apps/terminal.
// Verifies engine file integrity (sha256) and protocol major compatibility
// before spawning. Fail closed on tampered engine.
import { createHash } from "node:crypto";
import { readFileSync, existsSync } from "node:fs";

export function sha256File(path: string): string {
  const data = readFileSync(path);
  return createHash("sha256").update(data).digest("hex");
}

export function verifyEngine(enginePath: string, expectedSha256?: string): { ok: boolean; sha256: string; error?: string } {
  if (!existsSync(enginePath)) {
    return { ok: false, sha256: "", error: `engine not found: ${enginePath}` };
  }
  const actual = sha256File(enginePath);
  if (expectedSha256 && actual !== expectedSha256) {
    return { ok: false, sha256: actual, error: `checksum mismatch: expected ${expectedSha256.slice(0,16)} got ${actual.slice(0,16)} — refusing tampered engine` };
  }
  return { ok: true, sha256: actual };
}

export function checkProtocolCompat(enginePath: string, expectedMajor: number): { ok: boolean; found?: string; error?: string } {
  try {
    let found: string | null = null;
    for (const candidate of [enginePath, enginePath.replace("server.py", "protocol_generated.py")]) {
      try {
        const text = readFileSync(candidate, "utf8");
        const m = text.match(/PROTOCOL_VERSION\s*=\s*"([^"]+)"/);
        if (m) { found = m[1]; break; }
      } catch {}
    }
    if (!found) return { ok: true }; // no version to check
    const major = Number(found.split(".")[0]);
    if (major !== expectedMajor) {
      return { ok: false, found, error: `protocol major mismatch: engine ${found} vs expected ${expectedMajor}.x — regenerate with pnpm generate:protocol` };
    }
    return { ok: true, found };
  } catch (e) {
    return { ok: false, error: String(e) };
  }
}
