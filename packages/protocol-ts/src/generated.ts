// AUTO-GENERATED from packages/protocol-schema/protocol.json — do not hand-edit.
// Run: pnpm generate:protocol
export const PROTOCOL_VERSION = "0.1.0" as const;
export const PROTOCOL_MAJOR = 0 as const;
export const PROTOCOL_MINOR = 1 as const;

export const METHOD_NAMES = [
  "initialize",
  "capabilities",
  "health",
  "shutdown",
  "run.create",
  "run.list",
  "run.status",
  "run.cancel",
  "run.close",
  "event.subscribe",
  "tool.cancel",
  "provider.list",
  "model.list",
  "model.probe",
  "case.create",
  "case.list",
  "case.show",
  "case.update",
  "artifact.import",
  "evidence.list",
  "evidence.show",
  "evidence.redact",
  "coverage.get",
  "coverage.set",
  "report.build",
  "export.create",
  "run.rebuild",
  "policy.import",
  "policy.check",
  "policy.status",
  "scope.show",
  "chat.submit",
  "case.execute",
  "coach.score",
  "coverage.report",
  "tool.preview",
  "tool.execute",
  "approval.respond",
  "lab.list",
  "lab.launch",
  "lab.stop",
  "lab.reset",
  "experiment.compare",
  "evidence.diff",
  "mcp.add",
  "mcp.list",
  "mcp.enable",
  "mcp.disable",
  "mcp.remove",
  "tool.list",
  "tool.invoke",
  "har.import",
  "burp.import"
] as const;
export type MethodName = (typeof METHOD_NAMES)[number];

export const EVENT_NAMES = [
  "token.delta",
  "reasoning.summary",
  "warning",
  "error",
  "run.checkpointed",
  "budget.changed"
] as const;
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
      `Incompatible protocol major: peer=${peer} local=${PROTOCOL_VERSION}. Regenerate with pnpm generate:protocol or upgrade the engine/launcher so majors match.`,
    );
  }
}

export function isKnownMethod(m: string): m is MethodName {
  return (METHOD_NAMES as readonly string[]).includes(m);
}

export function isKnownEvent(e: string): e is EventName {
  return (EVENT_NAMES as readonly string[]).includes(e);
}
