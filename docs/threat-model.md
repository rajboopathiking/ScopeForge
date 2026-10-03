# Threat model (summary, plan.md §16)

Trust boundaries: user intent → TUI → protocol → engine policy → executor/egress →
target; artifacts/evidence/MCP/tool output are **untrusted data**.

| Threat | Control (owner) |
|---|---|
| Prompt injection from target/artifact | Typed role outputs; policy outside model; untrusted-data wrapping |
| Model fabricates permission | Permission refs resolve only to persisted engagement decisions |
| Scope matcher bypass | Canonicalization + structured rules + DNS/IP checks + property corpus |
| Redirect / subresource escape | Re-authorize every hop; strip creds on origin change |
| DNS rebinding / SSRF | Resolve + pin via policy-aware transport; explicit private-range rules |
| Malicious MCP / tool manifest | Disabled by default; user review; sandbox; schema + hash checks |
| Shell / argument injection | No interpolation; typed argv; constrained wrappers |
| Secret leak to provider | Redaction before context; provider allowlist; local-only policy |
| Evidence contamination | Immutable originals; hashes; transformation provenance |
| Cross-run data leak | Run-scoped roots; provider context isolation; explicit imports |
| Budget bypass | Counters + capabilities at executor/egress layer |
| Crash → ambiguous result | Append-only events; atomic checkpoint; explicit inconclusive state |
| Supply chain | Locks; minimal deps; SBOM; provenance; signed releases |
| Plugin privilege creep | Version pin; permission diff; re-approval; fail closed |

Abuse cases (must-have tests in `tests/security/`): malicious page tries to
redefine policy; redirect chain leaves scope; DNS flips to 169.254.169.254;
archive traversal / symlink escape; tool description injects approval text;
secret seeded in artifact must not reach logs/model/UI/export.

Security-sensitive owners: policy, egress, sandbox, secrets, updater,
archive parsing, export redaction. Two-person review when possible.
