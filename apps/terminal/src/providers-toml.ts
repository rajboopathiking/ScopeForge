// providers.toml: configured providers (secret REFERENCES only, never values).
// Mirrors the engine's loader (server.py load_provider_config); both sides parse
// the same minimal shape so CLI and engine agree without a round trip.
import { existsSync, mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { join } from "node:path";

export const PROVIDER_TYPES = ["mock", "openai-compat", "deepseek", "anthropic-compat"] as const;
export type ProviderType = (typeof PROVIDER_TYPES)[number];

export interface ProviderConfig {
  type: ProviderType;
  base_url?: string;
  credential?: string;
}

export function providersFile(home: string): string {
  return join(home, "providers.toml");
}

/** Parse the [providers.name] sections (minimal TOML subset: strings only). */
export function readProviders(home: string): Record<string, ProviderConfig> {
  const out: Record<string, ProviderConfig> = {};
  const file = providersFile(home);
  if (!existsSync(file)) return out;
  let current: string | null = null;
  try {
    for (const raw of readFileSync(file, "utf8").split("\n")) {
      const line = raw.trim();
      const section = /^\[providers\.([A-Za-z0-9_-]+)\]$/.exec(line);
      if (section) {
        current = section[1];
        out[current] = { type: "openai-compat" };
        continue;
      }
      if (!current || line === "" || line.startsWith("#")) continue;
      const kv = /^([A-Za-z_][\w]*)\s*=\s*"([^"]*)"\s*$/.exec(line);
      if (!kv) continue;
      const [, k, v] = kv;
      if (k === "type" && (PROVIDER_TYPES as readonly string[]).includes(v)) {
        out[current].type = v as ProviderType;
      } else if ((k === "base_url" || k === "credential") && v) {
        if (k === "base_url") out[current].base_url = v;
        else out[current].credential = v;
      }
    }
  } catch {
    return {};
  }
  return out;
}

export function validateProviderConfig(cfg: Partial<ProviderConfig>): string[] {
  const errs: string[] = [];
  if (!cfg.type || !(PROVIDER_TYPES as readonly string[]).includes(cfg.type)) {
    errs.push(`type must be one of ${PROVIDER_TYPES.join(", ")}`);
  }
  if (cfg.credential !== undefined && cfg.credential !== "") {
    const m = /^(env|keychain|cmd):.+/.exec(cfg.credential);
    if (!m) errs.push("credential must be a reference: env:VAR, keychain:svc/acct, or cmd:program");
    if (/^(sk-|AKIA|ghp_|xoxb-)/.test(cfg.credential)) {
      errs.push("refusing to store a secret VALUE; pass a reference (env:VAR), never the key");
    }
  }
  if (cfg.base_url !== undefined && cfg.base_url !== "" && !/^https?:\/\//.test(cfg.base_url)) {
    errs.push("base_url must start with http:// or https://");
  }
  return errs;
}

/** Write one [providers.name] section. No secret values are ever accepted (validated). */
export function writeProvider(
  home: string, name: string, cfg: ProviderConfig, force = false,
): { ok: boolean; error?: string } {
  if (!/^[A-Za-z0-9_-]+$/.test(name)) return { ok: false, error: "name must match [A-Za-z0-9_-]+" };
  const errs = validateProviderConfig(cfg);
  if (errs.length > 0) return { ok: false, error: errs.join("; ") };
  const existing = readProviders(home);
  if (existing[name] && !force) {
    return { ok: false, error: `provider '${name}' exists (use --force to overwrite)` };
  }
  mkdirSync(home, { recursive: true });
  const lines = [
    `[providers.${name}]`,
    `type = "${cfg.type}"`,
    ...(cfg.base_url ? [`base_url = "${cfg.base_url}"`] : []),
    ...(cfg.credential ? [`credential = "${cfg.credential}"`] : []),
    "",
  ];
  const file = providersFile(home);
  const prev = existsSync(file) ? readFileSync(file, "utf8") : "";
  // Drop any previous section with the same name, keep everything else byte-identical.
  const kept = prev
    .split(/(?=^\[providers\.)/m)
    .filter((chunk) => !new RegExp(`^\\[providers\\.${name}\\]`, "m").test(chunk));
  writeFileSync(file, `${kept.join("").replace(/\n*$/, "\n")}${lines.join("\n")}`);
  return { ok: true };
}
