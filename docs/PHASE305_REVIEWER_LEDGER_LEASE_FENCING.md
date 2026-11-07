# Phase 305 — Reviewer ledger lease fencing

## Scope

The reviewer ledger accepts an optional `ReviewerLeaseAuthority`. When
configured, both claim and submit require a currently valid lease token and
generation. Existing deployments without a lease authority retain their
previous behavior, allowing a staged migration.

## Atomicity and fencing

The lease registry and reviewer ledger must share the same SQLite database
file. Ledger claim/submission transitions execute under `BEGIN IMMEDIATE`;
lease renewals and recoveries also write under SQLite transactions. This
serializes a ledger transition against a concurrent lease mutation. Validation
checks the current persisted nonce, expiration, reviewer, artifact, task, and
generation. A renewal invalidates its previous token; recovery increments the
generation and fences prior tokens.

## Execution adapter

The host execution adapter now accepts optional lease token/generation
arguments and forwards them to both claim and submit. A caller using a
lease-enabled ledger must supply the same current lease at execution time.

## Deliberate limitation

This phase fences claims and submissions but does not yet change the reviewer
task's assigned reviewer identity or automatically recover the task state.
Recovery to a different reviewer requires an explicit ledger assignment
transition and lifecycle-integrity migration. Do not reassign a task merely by
changing the lease row.

## Tests

Focused tests cover required leases, superseded-generation rejection, valid
current-generation submission, and shared-database enforcement. Authored but
not executed.
