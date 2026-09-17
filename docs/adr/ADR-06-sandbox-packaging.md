# ADR-06: Sandbox & packaging

Date: 2026-09-16. Status: accepted. Implements plan.md §7.6 + §17.

- Built-in HTTP/browser tools run in-engine behind the policy egress proxy.
- External binaries: rootless container / platform sandbox, read-only root, dedicated
  scratch dir, no host network by default, bounded CPU/mem/files/duration, allowlisted
  egress proxy. No shell interpolation (argv arrays + typed schemas).
- Tool manifests declare exe hashes/versions, FS access, egress needs, risk class,
  output parser, cancellation behavior.
- Target content + tool descriptions are untrusted data; cannot register tools or
  mutate policy. MCP servers disabled by default per run (explicit trust per server).
- Distribution: npm launcher + signed per-OS/arch engine archives; launcher verifies
  checksum/signature + protocol compatibility; pipx/uv path; Homebrew/Windows plan;
  offline install from verified artifacts; SBOM + provenance + signed releases;
  opt-in anonymous-minimal update checks, never run data.
