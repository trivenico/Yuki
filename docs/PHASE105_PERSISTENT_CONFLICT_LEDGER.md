# Phase 105 — Persistent conflict ledger

## Scope
- Store conflict records and all candidate versions in local SQLite.
- Make repeated identical conflict ingestion idempotent; reject changed content for an existing conflict ID.
- Require an explicit actor and a candidate version ID for resolution.
- Preserve an immutable resolution and append an audit event.
- Provide bounded open-conflict and audit queries.
- Add SQLite persistence, idempotency, tamper/conflicting write, resolution and validation tests.

## Safety boundary
This module is local-only and is not connected to network sync or the live memory repository. It does not choose a winning version automatically, edit notes, or transmit conflict contents. Use an access-controlled local path because conflict versions may contain personal data.

## Remaining work
- Connect the deterministic conflict classifier to this ledger after both PRs are reviewed.
- Add integration tests for event-journal ingestion and conflict persistence.
- Add authenticated device pairing, offline reconciliation, encrypted backups and tested restore.
