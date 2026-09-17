// Home directory + minimal config handling. Portable files live under
// `$SCOPEFORGE_HOME` (default: `<cwd>/.scopeforge`). Phase 4 adopts this layout.
import { existsSync, mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { join, resolve } from "node:path";

export const HOME_VERSION = "0.1.0";

export function homeDir(cwd: string, env: NodeJS.ProcessEnv = process.env): string {
  const h = env.SCOPEFORGE_HOME;
  return h ? resolve(h) : join(resolve(cwd), ".scopeforge");
}

export interface HomeConfig {
  theme?: string;
  color?: boolean;
  engine?: string; // override path to server.py
}

export function ensureHome(home: string): void {
  mkdirSync(join(home, "runs"), { recursive: true });
  const cfg = join(home, "config.toml");
  if (!existsSync(cfg)) {
    writeFileSync(
      cfg,
      `# ScopeForge home (v${HOME_VERSION}). Secrets are referenced, never stored here.\n` +
        `theme = "default"\ncolor = true\n`,
    );
  }
  const keys = join(home, "keybindings.json");
  if (!existsSync(keys)) writeFileSync(keys, "{}\n");
}

/** Minimal TOML-subset reader: top-level `key = "str" | true | false | 123`. */
export function readConfig(home: string): HomeConfig {
  const out: HomeConfig = {};
  try {
    for (const line of readFileSync(join(home, "config.toml"), "utf8").split("\n")) {
      const m = /^\s*([A-Za-z_][\w]*)\s*=\s*(?:"([^"]*)"|(true|false)|(\d+))\s*(?:#.*)?$/.exec(line);
      if (!m) continue;
      const [, k, str, bool, num] = m;
      if (k === "theme" && str) out.theme = str;
      else if (k === "color" && bool) out.color = bool === "true";
      else if (k === "engine" && str) out.engine = str;
      void num;
    }
  } catch {
    /* missing config -> defaults */
  }
  return out;
}

export function runDir(home: string, runId: string): string {
  return join(home, "runs", runId);
}
