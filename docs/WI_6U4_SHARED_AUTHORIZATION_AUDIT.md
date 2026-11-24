# WI-6U4 — Shared authorization audit repository design

## Status
Architecture contract only. This is not an implementation or production-readiness claim.

## Problem
`AuthorizationGateway` accepts a callable audit writer, but `ProductionSecurityContext` currently requires `SQLiteAuthorizationAuditLog`. The monitor imports denied decisions by reading that local SQLite path. Therefore a PostgreSQL monitor does not make authorization decisions or their hash chain shared across replicas.

Removing the concrete type check alone is unsafe. The independent audit anchor verifies an ordered event ID and hash. Concurrent writers must not fork the chain, publish gaps, or overwrite a newer anchor.

## Required repository contract
The backend-neutral contract must provide:
- append a typed decision and return its committed event ID;
- verify the complete chain; read head and consistent checkpoint;
- verify and publish checkpoints against the independent anchor;
- bounded ordered event pages after an event ID;
- backup/export verification with backend-specific semantics.

Keep SQLite as the single-host adapter and add PostgreSQL as an explicit shared adapter. No implicit fallback.

## PostgreSQL append correctness
Each append must run in one transaction, acquire one global transaction-scoped advisory lock, read the committed head, derive the next contiguous committed event ID using a transactional counter (not a normal sequence that can leave rollback gaps), compute the exact existing canonical hash, insert, and commit before returning. All writers use the same lock and lock order. Aborts must not create cursor-visible holes.

## Hash compatibility and migration
Canonical field names, normalization, JSON sort order, separators, UTF-8 encoding, and SHA-256 semantics are compatibility boundaries. Add fixed test vectors shared by SQLite and PostgreSQL. Migration must validate the full SQLite chain, preserve every event ID and field/hash exactly, import through a controlled transactional or resumable staging process, then verify PostgreSQL chain and anchor before cutover. Never renumber or rewrite history.

## Independent anchor
The anchor remains outside the PostgreSQL trust boundary. Checkpoint reads are consistent. Publication uses compare-and-set/ETag semantics and verifies the returned receipt. Stale publishers must not overwrite newer checkpoints. Missing, malformed, stale, or inconsistent anchors fail closed. Never initialize, repair, or roll back an anchor automatically.

## Monitor ingestion
The monitor reads bounded ordered pages from the authoritative repository, not a per-replica SQLite path in PostgreSQL mode. Cursor advancement occurs only after the whole page is imported successfully. Signal IDs remain deterministic and idempotent. Only minimized security signals are copied into incident storage.

## Runtime integration
Keep the gateway dependent on an append interface. Replace the SQLite-only production-context check only after the PostgreSQL repository satisfies the full lifecycle contract and acceptance suite. Add explicit backend factory, readiness, bounded pool configuration, and close hooks. Do not place database or anchor credentials in model-controlled configuration. SQLite backup verification must not be presented as PostgreSQL backup verification.

## Acceptance criteria
1. Concurrent appends across independent pools produce one contiguous event sequence and valid chain.
2. Rolled-back appends consume no visible event ID.
3. Both backends produce identical hashes for fixed canonical vectors.
4. A second pool immediately reads and verifies committed events.
5. Tampered, deleted, reordered, duplicated, or missing events fail verification.
6. Anchor verification accepts a valid prefix and rejects mismatches, gaps, stale checkpoints, and unavailable anchors.
7. Competing publishers cannot overwrite a newer anchor with stale state.
8. Monitor paging is bounded, ordered, idempotent, and cursor-safe across crashes/lease expiry.
9. Migration preserves exact IDs and hashes and verifies before cutover.
10. Production context rejects unverified repositories and never silently downgrades.
11. PostgreSQL integration and full repository gates pass in CI.
12. Operators perform restore/failover and independent-anchor recovery drills before multi-host approval.

## Rollout
1. Land contract and compatibility vectors.
2. Implement PostgreSQL repository and migration.
3. Prove concurrency, rollback, chain, anchor, and monitor behavior on real PostgreSQL.
4. Add explicit factory/readiness integration.
5. Rehearse export/migration/rollback against production-like data.
6. Switch deployment only after verified anchor and rollback plan.
7. Block multi-host rollout until CI and operational evidence exists.

## Non-goals
No anchor replacement by the same database, no claim that a hash chain prevents privileged full-chain rewriting, no exactly-once external delivery, no automatic trust initialization/repair, and no multi-instance readiness claim based solely on unit tests.
