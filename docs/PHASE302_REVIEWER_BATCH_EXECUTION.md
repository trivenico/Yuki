# Phase 302 — Controlled reviewer batch execution

## Scope

Adds a synchronous, bounded batch runner for already-provisioned reviewer tasks.
It calls the host execution adapter sequentially, preserving per-task identity,
session validation, evidence bounds, result validation, and ledger signing.

## Guarantees

- No background scheduler or implicit task discovery.
- Batch size is bounded (maximum 100; default 25).
- Duplicate task IDs and malformed task tuples are rejected.
- Actor identity is resolved separately for each assigned reviewer.
- A task failure is isolated and recorded in the returned failure list;
  later tasks continue.
- The runner does not mint credentials, access signing keys, bypass ledger
  authorization, or submit verification reports.

## Caller responsibilities

The caller must provide current ledger versions, the provisioned task contracts,
a trusted reviewer-to-actor resolver, and the isolated executor callable.
For Docker isolation, pass the DockerReviewerExecutor returned by
`build_isolated_reviewer_adapter` as the executor argument. A failed task may
remain claimed if the ledger cannot persist the host-generated blocked result;
operational recovery remains a separate ledger/supervisor responsibility.

## Verification

Focused tests cover ordered execution, failure isolation, and duplicate task
rejection. They are authored but have not been run in a Python environment.
