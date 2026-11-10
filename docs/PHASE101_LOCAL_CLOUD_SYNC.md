# Phase 101 — Local/cloud sync journal

## Goal

Keep the owner's local Yuki and Oracle Cloud Yuki consistent while allowing each runtime to be temporarily offline. Local and cloud installations have distinct stable origin IDs. Changes are represented as immutable events with globally unique event IDs and per-origin sequence numbers.

## Implemented in this phase

- A SQLite append-only journal for bounded JSON events.
- Idempotent event ingestion: replaying identical event content is a no-op.
- Conflicting event IDs or origin/sequence pairs are rejected, never overwritten.
- Per-origin cursors and bounded incremental export.
- Regression tests for round-trip replication, duplicate delivery, tampering, conflicts, limits, and origin binding.

## Explicit non-goals / not yet implemented

This module is a storage primitive, not a complete synchronization service. It does not open a network listener, authenticate a remote peer, encrypt payloads, synchronize existing Yuki memory automatically, merge edits to the same logical note, replicate task leases, or make backups. It is not wired into the application runtime. Do not expose it directly to a network.

## Intended rollout architecture

1. Keep the cloud's durable store as the shared coordinator for task ownership and task leases.
2. Assign each installation a stable, unique origin ID. Never clone a database with its origin identity to a second active installation.
3. Authenticate devices with separate revocable credentials over TLS and the private access layer.
4. Exchange only validated, bounded event batches; acknowledge cursors only after durable commit.
5. Keep immutable history and detect conflicting logical edits. Prefer preserving both versions and asking the owner to resolve a conflict over last-write-wins.
6. Encrypt data in transit and backups; minimize cloud-synced data by explicit allowlists.
7. Test offline queueing, repeated delivery, out-of-order delivery, conflicting edits, revoked devices, interrupted transfers, restore, and disk exhaustion.

## Backups and durability

Replication is not a backup: a deletion or malicious edit can replicate too. Keep independent encrypted, versioned backups and test restores. Do not synchronize live SQLite database files by ordinary file-copy tools while either runtime is writing; use the SQLite backup API or a coordinated snapshot.

## Current status

Phase 101 adds journal primitives and tests only. No endpoint, device pairing, cloud instance, tunnel, runtime integration, or automatic sync is deployed. The project remains not production-ready.
