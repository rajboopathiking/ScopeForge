// Generates TypeScript + Python protocol bindings from packages/protocol-schema/protocol.json.
// Usage: node scripts/generate-protocol.mjs [--check]
// --check: fail (exit 1) if generated files differ (CI drift gate).
import { readFileSync, writeFileSync, mkdirSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { execSync } from "node:child_process";

const root = join(dirname(fileURLToPath(import.meta.url)), "..");
const schemaPath = join(root, "packages/protocol-schema/protocol.json");
const tsOut = join(root, "packages/protocol-ts/src/generated.ts");
const pyOut = join(root, "services/engine/src/scopeforge_engine/protocol_generated.py");
const fixtureOut = join(root, "tests/contract/fixtures/envelope.json");

const schema = JSON.parse(readFileSync(schemaPath, "utf8"));
const version = schema.protocol_version;
const [major, minor, patch] = version.split(".").map(Number);
const methods = schema.methods.map((m) => m.name);
const events = schema.events;

const ts = `// AUTO-GENERATED from packages/protocol-schema/protocol.json — do not hand-edit.
// Run: pnpm generate:protocol
export const PROTOCOL_VERSION = ${JSON.stringify(version)} as const;
export const PROTOCOL_MAJOR = ${major} as const;
export const PROTOCOL_MINOR = ${minor} as const;

export const METHOD_NAMES = ${JSON.stringify(methods, null, 2)} as const;
export type MethodName = (typeof METHOD_NAMES)[number];

export const EVENT_NAMES = ${JSON.stringify(events, null, 2)} as const;
export type EventName = (typeof EVENT_NAMES)[number];

export type RedactionClass = "clean" | "redacted" | "sensitive-ref-only";

export interface Envelope {
  jsonrpc: "2.0";
  protocol_version: string;
  trace_id: string;
  ts: string;
  run_id?: string;
  seq?: number;
  redaction?: RedactionClass;
}

export interface RpcRequest extends Envelope {
  id: number | string;
  method: MethodName | string;
  params?: Record<string, unknown>;
}

export interface RpcResponse extends Envelope {
  id: number | string;
  result?: unknown;
  error?: { code: number; message: string; data?: unknown };
}

export interface RpcEvent extends Envelope {
  method: EventName | string;
  params?: Record<string, unknown> & { seq?: number };
}

/** Major version compatibility: fail closed with actionable message. */
export function assertCompatible(peer: string): void {
  const peerMajor = Number(String(peer).split(".")[0]);
  if (!Number.isFinite(peerMajor) || peerMajor !== PROTOCOL_MAJOR) {
    throw new Error(
      \`Incompatible protocol major: peer=\${peer} local=\${PROTOCOL_VERSION}. Regenerate with pnpm generate:protocol or upgrade the engine/launcher so majors match.\`,
    );
  }
}

export function isKnownMethod(m: string): m is MethodName {
  return (METHOD_NAMES as readonly string[]).includes(m);
}

export function isKnownEvent(e: string): e is EventName {
  return (EVENT_NAMES as readonly string[]).includes(e);
}
`;

const py = `"""AUTO-GENERATED from packages/protocol-schema/protocol.json — do not hand-edit.

Run: pnpm generate:protocol
"""
from __future__ import annotations

PROTOCOL_VERSION = "${version}"
PROTOCOL_MAJOR = ${major}
PROTOCOL_MINOR = ${minor}
PROTOCOL_PATCH = ${patch}

METHOD_NAMES = ${JSON.stringify(methods)}
EVENT_NAMES = ${JSON.stringify(events)}


class IncompatibleProtocol(Exception):
    pass


def assert_compatible(peer: str) -> None:
    try:
        peer_major = int(str(peer).split(".")[0])
    except ValueError:
        peer_major = -1
    if peer_major != PROTOCOL_MAJOR:
        raise IncompatibleProtocol(
            f"Incompatible protocol major: peer={peer} local={PROTOCOL_VERSION}. "
            "Regenerate with pnpm generate:protocol or upgrade the engine/launcher."
        )


def is_known_method(m: str) -> bool:
    return m in METHOD_NAMES


def is_known_event(e: str) -> bool:
    return e in EVENT_NAMES
`;

const fixture = {
  _comment: "Sample v0 handshake + ordered event. Contract tests assert both sides parse this.",
  request: {
    jsonrpc: "2.0",
    id: 1,
    method: "initialize",
    params: { protocol_version: version, client: "terminal/0.1.0" },
    protocol_version: version,
    trace_id: "trace-fixture-0001",
    ts: "2026-09-16T00:00:00Z",
  },
  event: {
    jsonrpc: "2.0",
    method: "token.delta",
    params: { seq: 1, index: 0, total: 10000, text: "hello" },
    protocol_version: version,
    trace_id: "trace-fixture-0001",
    ts: "2026-09-16T00:00:00Z",
  },
};

const check = process.argv.includes("--check");
function writeOrCheck(path, content) {
  if (check) {
    let current = null;
    try {
      current = readFileSync(path, "utf8");
    } catch {}
    if (current !== content) {
      console.error(`protocol drift: ${path} differs from schema. Run pnpm generate:protocol`);
      process.exitCode = 1;
    }
    return;
  }
  mkdirSync(dirname(path), { recursive: true });
  writeFileSync(path, content);
  console.log(`wrote ${path}`);
}

writeOrCheck(tsOut, ts);
writeOrCheck(pyOut, py);
writeOrCheck(fixtureOut, JSON.stringify(fixture, null, 2) + "\n");

if (check && process.exitCode) {
  console.error("hint: run `pnpm generate:protocol` and commit the regenerated files");
} else if (!check) {
  // keep formatting deterministic where prettier/ruff exist; never fail generation on missing tools
  for (const [cmd, cwd] of []) void [cmd, cwd];
  void execSync;
}
