// Session journal: client-side event log enabling crash-resume of the displayed
// sequence. Writes `runs/<id>/events.jsonl` (Phase-4-compatible envelope lines);
// the engine-owned authoritative log replaces this client journal in Phase 4.
import { existsSync, mkdirSync, readFileSync, appendFileSync } from "node:fs";
import { join } from "node:path";
import type { RpcEvent } from "@scopeforge/protocol";
import { runDir } from "./home.js";

export function journalFile(home: string, runId: string): string {
  return join(runDir(home, runId), "events.jsonl");
}

/** Append one received event envelope. Best-effort; never throws. */
export function journalAppend(home: string, runId: string, ev: RpcEvent): void {
  try {
    mkdirSync(runDir(home, runId), { recursive: true });
    appendFileSync(journalFile(home, runId), JSON.stringify(ev) + "\n");
  } catch {
    /* display must never crash on journal I/O */
  }
}

/** Replay the journal: returns events in file order plus the max seen seq. */
export function journalReplay(home: string, runId: string): { events: RpcEvent[]; maxSeq: number } {
  const f = journalFile(home, runId);
  const events: RpcEvent[] = [];
  let maxSeq = 0;
  if (!existsSync(f)) return { events, maxSeq };
  for (const line of readFileSync(f, "utf8").split("\n")) {
    if (!line.trim()) continue;
    try {
      const ev = JSON.parse(line) as RpcEvent;
      events.push(ev);
      const s = (ev.params as { seq?: number } | undefined)?.seq;
      if (typeof s === "number" && s > maxSeq) maxSeq = s;
    } catch {
      /* skip */
    }
  }
  return { events, maxSeq };
}

/** Merge two ordered event lists, deduping by seq (crash-resume may re-emit). */
export function mergeEvents(a: RpcEvent[], b: RpcEvent[]): RpcEvent[] {
  const seen = new Set<number>();
  const out: RpcEvent[] = [];
  for (const ev of [...a, ...b]) {
    const s = (ev.params as { seq?: number } | undefined)?.seq;
    if (typeof s === "number") {
      if (seen.has(s)) continue;
      seen.add(s);
    }
    out.push(ev);
  }
  return out;
}
