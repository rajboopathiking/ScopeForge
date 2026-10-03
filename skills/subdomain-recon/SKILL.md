---
name: subdomain-recon
description: Passive OSINT and Certificate Transparency reconnaissance for authorized
  asset discovery.
triggers:
- subdomain
- dns
- crt.sh
- ct logs
- asset discovery
- recon
author: ScopeForge
version: 1.0.0
---

### Subdomain & Asset Discovery Methodology

1. **Passive First**: Query Certificate Transparency logs (crt.sh) and public DNS records before active probing.
2. **Boundary Validation**: Check every discovered hostname against the ScopeGate authorized target list.
3. **CNAME Mapping**: Check for dangling DNS records (e.g. GitHub Pages, S3, Heroku) pointing to unallocated resources (Subdomain Takeover risk).
4. **Telemetry Preservation**: Save discovered domain lists into `.scopeforge/evidence/`.
