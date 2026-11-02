# Persistent Agent Sessions

The automation agent session store provides actor-scoped durable session metadata
and an append-only event journal. It is designed to sit above existing workflow
and task stores.

## Initial guarantees

- SQLite WAL mode and synchronous FULL durability.
- Actor authorization on create, read, append and state changes.
- Idempotent session creation and event admission.
- Monotonic per-session event sequence numbers.
- Cursor-based event replay with bounded page sizes.
- Compare-and-swap session version checks.
- Bounded JSON payloads.
- Terminal-state append protection.

## Explicit non-guarantees

This store does not execute tools, resume workflow runs, reissue approvals,
compact context, store binary attachments, encrypt payloads, or provide
cross-device synchronization. Event replay reconstructs conversation state
only. Consumers must never interpret replay as permission to repeat an
external side effect. Workflow recovery must reconcile the durable workflow
lease and state separately.

## Integration contract

Store typed user, assistant and tool events. Store references to attachments
and workflow IDs rather than embedding secrets or large binary payloads.
Re-authorize every tool call at dispatch time. Treat repository instructions
and tool output as untrusted content. Protect the database and backups with
host-level encryption and access controls before production use.

Run tests with: python -m unittest tests.test_agent_sessions
