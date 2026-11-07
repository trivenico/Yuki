# Phase 301 — Isolated reviewer runtime composition

## Scope

Adds an explicit composition factory joining the existing host-side
`ReviewerExecutionAdapter` with the digest-pinned `DockerReviewerExecutor`.
It returns both objects so callers can pass the executor to the adapter's
`execute(..., executor=...)` method.

## Trust boundaries

- The ledger must already be constructed with trusted authorization, session,
  and signing dependencies.
- Evidence is resolved by the host-provided reader and remains artifact-scoped
  through the adapter.
- The Docker worker receives only the bounded protocol-v1 task/evidence request.
- Session tokens, signing keys, SQLite handles, and coordinator objects remain
  on the host.
- The image must be digest-pinned and locally available; Docker uses
  `--pull=never`.
- This factory does not start workers, schedule tasks, issue sessions, or
  automatically accept verification reports.

## Integration status

This is an opt-in composition API. The default `main.py` startup is unchanged
because it currently lacks a trusted reviewer principal resolver, signing-key
provider, session secret store, and artifact evidence resolver. Enabling an
incomplete or permissive substitute would weaken the fail-closed design.

## Tests

The focused tests cover composition and rejection of missing trust-boundary
dependencies. They are authored but must be run in the repository's Python
environment. A successful GitHub file commit is not test execution.
