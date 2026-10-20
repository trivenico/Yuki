# Phase 37 — Plugin discovery hardening

## Audit finding

The plugin loader imports Python modules in the main process. Python module import executes top-level code with the Yuki process's privileges, so a trust list is not a sandbox. The prior loader also accepted wildcard trust and coerced arbitrary truthy values into its enabled flag.

## Changes

- Plugin discovery remains disabled by default.
- Enabling discovery without an explicit list of trusted modules now loads nothing.
- Wildcard trust (`*`) is rejected; operators must enumerate exact module names.
- A string cannot be accidentally treated as an iterable allowlist.
- Non-boolean enabled values, malformed module names, and duplicate allowlist entries fail closed.
- Added regression tests for the above configuration boundaries.

## File organization

This phase keeps the security boundary in `core/plugin_loader.py`, places focused regression tests in `tests/test_phase37_plugin_loader.py`, and records the phase audit in `docs/PHASE37_PLUGIN_DISCOVERY_HARDENING.md`. No broad file moves were made: moving modules before dependency/import mapping is complete would create unnecessary production risk.

## Important limitation

This is configuration hardening, not process isolation. Trusted plugin imports still execute in-process. Do not enable untrusted or generated plugin code. The next phase should introduce a narrow invocation facade and an OS-isolated worker process with bounded IPC, timeouts, resource limits, explicit capability grants, and kill-on-timeout behavior.

## Verification status

The change has been committed to `phase6/production-hardening`. Run the repository's full CI against the latest head before treating the phase as verified. The previous Phase 36 workflow does not validate this newer commit.

## Readiness

Do not raise the 40% engineering readiness estimate solely on this change. System-wide bypass inventory, durable hash-bound approvals, process isolation, provider/MCP parity, full release checks, and independent security review remain open.
