import { describe, expect, it } from "vitest";
import { readFileSync } from "node:fs";
import { join, dirname } from "node:path";
import { fileURLToPath } from "node:url";
import {
  containsSecret,
  redactText,
  redactValue,
  REDACTED,
} from "../../packages/protocol-ts/src/redact.js";

const root = join(dirname(fileURLToPath(import.meta.url)), "../..");
const fullText = readFileSync(join(root, "tests/fixtures/secrets.txt"), "utf8");
// PEM bodies only match as a block; everything else must match per line.
const pemRange = (() => {
  const lines = fullText.split("\n");
  const b = lines.findIndex((l) => l.includes("BEGIN"));
  const e = lines.findIndex((l) => l.includes("END"));
  return new Set(lines.slice(b, e + 1));
})();
const seeds = fullText
  .split("\n")
  .filter((l) => l.trim().length > 0 && !pemRange.has(l));

describe("display redaction (defense in depth)", () => {
  it("every seeded secret shape is detected", () => {
    expect(seeds.length).toBeGreaterThan(10);
    for (const s of seeds) expect(containsSecret(s)).toBe(true);
  });

  it("redaction removes all seeded secrets, keeps labels", () => {
    for (const s of seeds) {
      const r = redactText(s);
      expect(r.redactions).toBeGreaterThan(0);
      for (const seed of seeds) {
        // long distinctive tokens must not survive (short generic words may)
        if (seed.length > 24 && !/^(password|api_key)/.test(seed)) {
          expect(r.text).not.toContain(seed);
        }
      }
      expect(containsSecret(r.text)).toBe(false);
    }
    // assignment labels survive with redacted values
    const labeled = redactText('password = "supersecret-seed-password"').text;
    expect(labeled).toContain("password");
    expect(labeled).toContain(REDACTED);
    expect(labeled).not.toContain("supersecret-seed-password");
  });

  it("PEM blocks are caught document-wide (BEGIN..END as one unit)", () => {
    const r = redactText(fullText);
    expect(r.rules).toContain("private-key");
    expect(r.text).not.toContain("SEEDPRIVATEKEYBLOCK");
    expect(r.text).not.toContain("BEGIN RSA PRIVATE KEY");
    expect(containsSecret(r.text)).toBe(false);
  });

  it("benign prose passes through byte-identical", () => {    const benign = [
      "hello world",
      "T02.1 member from tenant B cannot export tenant A project",
      "expected 403, observed 403 with error-only body",
      "run run_0001 [artifacts] draft ev=25",
    ];
    for (const b of benign) {
      const r = redactText(b);
      expect(r.text).toBe(b);
      expect(r.redactions).toBe(0);
    }
  });

  it("redactValue kills secret-named keys and nested shapes", () => {
    const { value, redactions } = redactValue({
      headers: { authorization: "Bearer seedbearertoken0123456789abcdef", "x-tenant": "t-b" },
      body: { note: "nothing here", key: "AKIAIOSFODNN7SEEDKE1" },
    });
    expect(redactions).toBeGreaterThan(0);
    expect(JSON.stringify(value)).not.toContain("seedbearertoken");
    expect(JSON.stringify(value)).not.toContain("AKIAIOSFODNN7SEEDKE1");
    expect((value as { headers: Record<string, string> }).headers["x-tenant"]).toBe("t-b");
  });
});
