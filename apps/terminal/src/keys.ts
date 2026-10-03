// Keybindings: complete keyboard operation, user-configurable via
// `.scopeforge/keybindings.json` (partial overlay of action -> key names).
import { existsSync, readFileSync } from "node:fs";
import { join } from "node:path";

export type Action =
  | "up" | "down" | "left" | "right" | "select" | "palette"
  | "approve" | "deny" | "quit" | "help" | "composer";

export const DEFAULT_KEYS: Record<Action, string[]> = {
  up: ["k", "up"],
  down: ["j", "down"],
  left: ["h", "left"],
  right: ["l", "right"],
  select: ["enter"],
  palette: ["/", "ctrl+p"],
  approve: ["a"],
  deny: ["d"],
  quit: ["q", "ctrl+c"],
  help: ["?"],
  composer: ["i", ":"],
};

export type Keymap = Record<Action, string[]>;

export function loadKeymap(home: string): Keymap {
  const map = Object.fromEntries(
    Object.entries(DEFAULT_KEYS).map(([k, v]) => [k, [...v]]),
  ) as Keymap;
  try {
    const f = join(home, "keybindings.json");
    if (!existsSync(f)) return map;
    const overlay = JSON.parse(readFileSync(f, "utf8")) as Partial<Record<Action, string[] | string>>;
    for (const [action, keys] of Object.entries(overlay)) {
      if (!(action in map)) continue; // unknown actions ignored
      const list = Array.isArray(keys) ? keys : [keys];
      if (list.every((k) => typeof k === "string" && k.length > 0)) {
        map[action as Action] = list as string[];
      }
    }
  } catch {
    /* corrupt overlay -> defaults */
  }
  return map;
}

/** Normalize a raw keypress to a key name (arrows, enter, ctrl+x, printable). */
export function keyName(buf: Buffer): string {
  const s = buf.toString("utf8");
  if (s === "\r" || s === "\n") return "enter";
  if (s === "\u001b[A") return "up";
  if (s === "\u001b[B") return "down";
  if (s === "\u001b[C") return "right";
  if (s === "\u001b[D") return "left";
  if (s === "\u0003") return "ctrl+c";
  if (s === "\u0010") return "ctrl+p";
  if (s === "\u001b") return "esc";
  if (s.length === 1) return s.toLowerCase();
  return s;
}

export function actionFor(map: Keymap, key: string): Action | undefined {
  for (const [action, keys] of Object.entries(map)) {
    if ((keys as string[]).includes(key)) return action as Action;
  }
  return undefined;
}
