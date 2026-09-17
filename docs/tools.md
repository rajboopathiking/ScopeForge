# Tools (plan.md §7.5, §9.4)

## Risk classes

| Class | Examples | Default |
|---|---|---|
| R0 local read | read supplied file, parse schema, hash artifact | allow inside run directory |
| R1 local write | derived artifact, report, case | allow with audit event |
| R2 bounded passive network | exact policy/document URL | deferred to Phase-7 transport review |
| R3 live target read | one authorized GET/HEAD, navigation | policy gate + preview |
| R4 live state change | POST/PUT/upload/message | one-shot approval + fixture proof |
| R5 shared/high-impact | concurrency, cache, desync, costly | disabled without explicit isolation |
| R6 prohibited | persistence, stealth, unrelated-data extraction, destruction, cred attacks | no execution path |

## Live HTTP path (`tool.preview` → `approval.respond` → `tool.execute`)

1. `tool.preview` dry-runs the 12-step gate and returns allow/deny plus an
   exact approval preview (tool, destination, method, estimate, permission).
2. The TUI modal shows the preview; approving calls `approval.respond` with
   the preview's **scope**. `--yes` never bypasses this gate.
3. `tool.execute` mints a short-lived, single-purpose capability, verifies it,
   sends exactly once per hop, captures every hop as evidence, and consumes
   one-shot approvals on use. Tampered or broadened capabilities are refused
   before any packet leaves.
4. Redirects are re-decided per hop (max 5); credentials never cross origins;
   cookies stay partitioned by origin; non-idempotent methods never auto-retry;
   rate/concurrency/byte/cost budgets and stop signals are enforced below the
   agent, with transport failures reported as such (never as results).

## Baseline/variant experiments

`experiment.compare` rejects anything but exactly one changed variable across
method/url/actor/body/headers, executes both arms through the same gate, links
both evidence records to the case, and returns the diff (`evidence.diff`
recomputes it any time: structural JSON when both sides parse, unified text
otherwise).

## Browser (opt-in)

`uv sync --extra browser` enables Playwright capture. Navigation and every
subresource are gated independently; without the extra, construction fails
with an actionable message. WebSockets need explicit scope.

## Labs

`run start --lab tenancy-demo` launches a loopback lab, scopes a live run to
it with conservative budgets, and resets fixtures on launch. Labs are
engine-local tooling (audit-logged), never target contact.

## External-tool wrappers (Phase 8)

Third-party binaries run rootless-sandboxed with typed argv (never shell),
hash-pinned manifests, target/argument/output caps, and the same egress
capability. See the wrapper SDK: manifest, allowlisted args, bounded
resources, parsed-not-piped output.
