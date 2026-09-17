import { describe, expect, it } from "vitest";
import { readFileSync } from "node:fs";
import { join, dirname } from "node:path";
import { fileURLToPath } from "node:url";
import { PROTOCOL_VERSION, assertCompatible } from "../../packages/protocol-ts/src/generated.js";

const root = join(dirname(fileURLToPath(import.meta.url)), "../..");

describe("protocol contract", () => {
  it("schema source of truth matches generated TS constants", () => {
    const schema = JSON.parse(readFileSync(join(root, "packages/protocol-schema/protocol.json"), "utf8"));
    expect(PROTOCOL_VERSION).toBe(schema.protocol_version);
  });

  it("golden envelope fixture parses on both sides", () => {
    const fx = JSON.parse(readFileSync(join(root, "tests/contract/fixtures/envelope.json"), "utf8"));
    expect(fx.request.method).toBe("initialize");
    expect(fx.event.method).toBe("token.delta");
    expect(() => assertCompatible(fx.request.params.protocol_version)).not.toThrow();
  });

  it("golden T02 artifact-only case validates required one-variable shape", () => {
    const c = JSON.parse(
      readFileSync(join(root, "evals/golden/artifact-only-t02/case.json"), "utf8"),
    );
    for (const k of ["hypothesis", "expected_rule", "baseline", "changed_variable", "observable_result", "stopping_point"])
      expect(String(c[k]).length).toBeGreaterThan(0);
    expect(c.result).not.toBe("validated_finding");
    const variant = JSON.parse(
      readFileSync(join(root, "evals/golden/artifact-only-t02/variant.json"), "utf8"),
    );
    expect(variant.status).toBe(403);
    expect("project_id" in variant.body).toBe(false);
  });
});
