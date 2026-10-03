---
name: api-idor-audit
description: Techniques for detecting Broken Object Level Authorization (BOLA/IDOR)
  in REST and GraphQL APIs.
triggers:
- idor
- bola
- api
- graphql
- authorization bypass
- object level
author: ScopeForge
version: 1.0.0
---

### API BOLA / IDOR Testing Playbook

1. **Endpoint Identification**: Locate endpoints featuring resource identifiers (e.g. `/api/v1/orders/{order_id}`).
2. **Differential Testing**: Submit request with User A's auth token requesting User B's resource ID.
3. **Falsifiable Assertion**: Baseline (User A accesses Resource A -> 200 OK). Test (User A accesses Resource B -> Expected 403 Forbidden).
4. **Defensive Verification**: Confirm if tenant isolation is enforced at the database query layer.
