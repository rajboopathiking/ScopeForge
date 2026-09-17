// Stable CLI exit codes (plan.md §4.2). Documented in `--help` and docs.
export const EXIT = {
  OK: 0,
  FAIL: 1, // generic/unspecified failure
  USAGE: 2, // invalid usage, invalid config, missing input
  NOT_IMPLEMENTED: 3, // parsed command scheduled for a later phase
  POLICY_BLOCKED: 4, // deterministic policy gate refused
  APPROVAL_DECLINED: 5,
  PROVIDER: 6, // provider failure
  TOOL: 7, // tool failure
  BUDGET: 8, // budget exhausted
  PARTIAL: 9, // partial / inconclusive result
} as const;

export type ExitCode = (typeof EXIT)[keyof typeof EXIT];
