# Packaging and distribution (Phase 9)

ScopeForge is two local processes: a TypeScript CLI/TUI launcher and a Python engine.
Both are local-first, inspectable, and reproducible. No telemetry by default.

## Install

### One-line (npm + pipx)

```bash
npm install -g @scopeforge/terminal
pipx install scopeforge-engine   # or: uv tool install scopeforge-engine
# or: uv sync --extra browser   # for Playwright capture
```

Clean-machine install/uninstall is CI-tested on Linux, macOS, Windows.

### From source (offline-capable)

```bash
git clone https://github.com/org/scopeforge
pnpm install
uv sync --python 3.12 --all-extras
pnpm generate:protocol
```

Signed archives per OS/arch are published on GitHub Releases.

## Launcher verification

The `scopeforge` bin is a Node launcher that verifies the Python engine before
spawning:

- Computes `sha256(server.py)` and compares to `SCOPEFORGE_ENGINE_SHA256` (or
  the pinned hash in `scripts/launcher.mjs` for releases).
- Checks `PROTOCOL_VERSION` major compatibility (`pnpm generate:protocol` must
  match).
- **Fail closed** on tampered engine or major mismatch — actionable message,
  no launch.

Programmatic check:

```ts
import { verifyEngine, checkProtocolCompat } from "./launcher.js";
verifyEngine("/path/to/server.py", expectedSha256);
checkProtocolCompat("/path/to/server.py", 0);
```

Manual:

```bash
node scripts/launcher.mjs --engine services/engine/src/scopeforge_engine/server.py
node scripts/launcher.mjs --engine /path/to/server.py --expected-sha256 <hex>
```

## Version compatibility matrix

| Terminal | Engine | Protocol |
|---|---|---|
| 0.1.x | 0.1.x | 0.1.x (major must match; minor negotiates) |

Launcher refuses incompatible majors with an actionable message.

## SBOM / provenance / signing

```bash
uv run python scripts/sbom.py  # writes sbom.json (CycloneDX) + provenance.json
```

- `sbom.json` — CycloneDX 1.5, components from `pnpm-lock.yaml` + `uv.lock`
  plus the two first-party components.
- `provenance.json` — SLSA-style predicate with builder id and git commit.
- Release artifacts are signed (Sigstore cosign) and `provenance.json`
  is attested. CI generates SBOM/provenance on every tagged release.
- Dependency review and vulnerability scan run on PRs (see `.github/workflows/ci.yml`).

## Update policy

Update checks are opt-in or anonymous-minimal and never send run information
(prompts, targets, policy, evidence, model output, credentials, report content).

## Offline install

Download the verified archives from Releases, verify `sha256sum` + signature,
then install offline:

```bash
npm install ./scopeforge-terminal-0.1.0.tgz
pip install ./scopeforge_engine-0.1.0-py3-none-any.whl
```
