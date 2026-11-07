# Phase 310 — Durable reviewer dispatch outbox

## Scope

Adds a SQLite-backed transactional outbox for reviewer assignment delivery.
The outbox is written in the same `BEGIN IMMEDIATE` transaction as the
reviewer task, assignment event, and initial lease. This prevents a committed
assignment from silently lacking its dispatch record.

## Outbox lifecycle

`pending -> claimed -> delivered`

A failed delivery returns to `pending` with bounded exponential backoff, or
moves to `dead_letter` after the configured attempt budget. Expired claims are
reclaimed with a new random token. Tokens are stored only as SHA-256 digests;
the raw token is returned once to the assigned worker.

## Worker isolation

A worker can claim only records whose `reviewer_agent_id` equals its own
authenticated actor ID. The API rejects an actor/worker mismatch. Claim tokens
are bound to the dispatch record, worker identity, and unexpired claim window.
A stale worker cannot complete or fail a claim after it has been reclaimed.

Claim batches are bounded (1–100), claim leases are bounded (10–900 seconds),
retry budgets are bounded (1–20), and retry delay is capped at one hour.
Error codes are constrained to short machine-readable identifiers rather than
arbitrary exception text.

## Observability

Artifact-authorized listing exposes dispatch state, attempts, availability,
claim owner/expiry, timestamps, and a bounded error code. It never exposes the
claim-token digest. Provisioned reviewer tasks now include their durable
dispatch ID.

## Meaning of delivery acknowledgement

The `delivered` state means the assigned worker successfully claimed and
acknowledged receipt of the dispatch contract. It does **not** mean review
execution or review submission completed. Those remain represented by the
reviewer task ledger lifecycle.

## Security and durability boundaries

- Outbox records contain no session tokens, lease tokens, signing keys, or
  arbitrary worker-provided payloads.
- The queue returns the immutable task contract only to its assigned reviewer.
- A trusted host still needs to deliver the separately provisioned session
  and lease credentials to the worker over a protected transport.
- Credentials are intentionally not persisted in the outbox. After a process
  restart, the host must reconcile pending tasks with reviewer-session and
  lease authorities before executing them. Automated credential restoration
  and background polling are future work.
- The outbox does not execute agent code. It is a durable dispatch primitive,
  not a complete continuously running worker service.

## Tests

Added regression coverage for transactional outbox creation, assigned-worker
claiming, actor impersonation rejection, delivery acknowledgement, retry and
dead-letter behavior, and expired-claim token fencing. Tests are authored but
have not been executed.
