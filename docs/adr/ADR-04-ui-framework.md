# ADR-04: UI framework — OpenTUI React conditional on compatibility spike

Date: 2026-09-16. Status: **NO-GO (spike result, Phase 2)** — re-spike gated on upstream.

## Spike result (2026-09-16, ~30 min timebox of the 2-day budget)

- `@opentui/core@0.5.11` resolves and installs cleanly via pnpm.
- Minimal render smoke (`createCliRenderer` + `Box`/`Text`, piped stdin, macOS arm?/x64):
  **fails at init** on Node `v24.13.1` `darwin/x64`:
  `Failed to initialize OpenTUI render library: OpenTUI native FFI is not available for this runtime yet`.
- `bun` (OpenTUI's primary runtime) is not installed here, so no cross-check;
  Windows Terminal / tmux / screen-reader matrix could not start.
- npm CLI itself is broken in this env (`Cannot read properties of null`); pnpm works.

## Decision

- **Do not take the native dependency in Phase 2.** Build all TUI views against a
  small `Renderer` interface with a `plain` backend (ANSI + ASCII fallback, `--no-color`,
  `--plain` screen-reader mode, small-terminal compact layout). The plain backend
  simultaneously satisfies the Phase 2 gates for redirected stdout, no-TTY, and
  screen-reader plain mode.
- Keep view code renderer-agnostic (pure `render(state) -> lines` functions +
  snapshot/contract tests) so an OpenTUI/React binding can land later without
  rewriting views, approvals, or the command palette.
- Re-spike when: OpenTUI publishes stable Node FFI (no bun requirement) + Windows
  x64/arm64 prebuilds. Re-run the matrix from plan.md §4.1 before adopting.

## Consequences

- No native/FFI dependency in the install path; clean-machine install stays `pnpm + uv`.
- Full-screen mouse/keyboard richness is deferred; keyboard-first plain TUI must still
  meet: complete keyboard operation, no color-only meaning, high-contrast + monochrome
  themes, reduced-motion (no animations in plain backend), ASCII-safe symbols.
