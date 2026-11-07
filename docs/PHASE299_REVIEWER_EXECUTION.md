# Phase 299 — Host-managed reviewer execution adapter

## Implemented

Adds `ReviewerExecutionAdapter` to run one provisioned review through a
trusted host boundary.

- The host claims the task using its scoped reviewer session.
- Evidence access is limited to references listed in the immutable task and
  bound to the task artifact digest.
- Evidence readers must return bytes; per-reference and total byte limits are
  enforced.
- The reviewer callback receives only the task and a read-only mapping of
  assigned evidence bytes. It never receives the session token, signing key,
  ledger, or database handle.
- The callback must return a validated ReviewResult bound to the exact task.
- The host submits the result through the ledger; the ledger applies the
  reviewer signature and persists the state transition.
- Callback, evidence, and result-validation failures are recorded as a
  signed `blocked` result, rather than leaving a claimed task in progress.

## Trust boundary

The executor callback still runs in-process in this phase. This adapter is an
API boundary, not an OS sandbox. Do not pass untrusted plugin code into the
callback without an external isolation layer. The host's ledger authorization
and signer resolver must remain inaccessible to the callback. Evidence reader
implementations must independently verify that each reference belongs to the
specified artifact digest.

The blocked result means host-controlled execution could not complete; it is
not a reviewer finding and never counts as a passed check.

## Tests

Four tests cover scoped evidence, signed submission, worker failure, evidence
size limits, and pre-claim task binding. Tests are authored but not executed.
