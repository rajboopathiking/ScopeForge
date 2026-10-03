---
name: reverse-proxy-bypass
description: Auditing HTTP reverse proxy header rewrites, hop-by-hop headers, and
  path normalizations.
triggers:
- proxy
- hop-by-hop
- x-forwarded
- waf bypass
- path traversal
- nginx
author: ScopeForge
version: 1.0.0
---

### Reverse Proxy & Gateway Audit Playbook

1. **Header Normalization**: Test header casing and duplicate header behavior (e.g. `Transfer-Encoding`, `Host`).
2. **Hop-by-Hop Headers**: Check if `Connection: close, X-Forwarded-For` strips downstream authentication headers.
3. **Path Traversal Deltas**: Compare origin behavior for URI encoded sequences (`%2e%2e%2f`, `..;/`).
4. **ScopeGate Check**: Verify origin target remains within authorized engagement boundaries.
