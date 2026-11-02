# Phase 156 — Durable generation recovery

## Durable run records

Conversation user-message insertion can now create a generation-run record in the same SQLite transaction. This closes the crash window between recording a user turn and recording that generation began.

Each run records:
- Authenticated owner and conversation scope.
- The idempotent request ID and corresponding user-message ID.
- A constrained lifecycle state: running, completed, interrupted, failed or cancelled.
- A small JSON checkpoint (maximum 4 KiB).
- Start, update and terminal timestamps.

Checkpoints deliberately contain execution metadata only. Generated text, credentials, tool arguments and provider payloads are not copied into checkpoint storage.

## Recovery behavior

Before a new reply or idempotent retry proceeds, the service reconciles running records while holding the conversation generation lease.

- If an assistant response was already persisted before a crash, the run is marked completed.
- If the user message has no assistant response, the service appends a fixed, sanitized interrupted response and marks the run interrupted.
- An idempotent retry returns the persisted interrupted response.
- A new user turn can proceed after reconciliation.

## Side-effect safety

Recovery never replays the provider request or tool calls. A crash may occur after a tool has already caused an external side effect but before Yuki persisted the final assistant response. Automatically rerunning that work would risk duplicate actions. The user is asked to submit a new request instead.

## Operational boundary

This is durable interruption detection and safe transcript reconciliation, not automatic continuation from an arbitrary model-token checkpoint. True resumable execution requires tools with durable idempotency keys and explicit checkpoint contracts, plus provider-specific resumability where available.

The current store remains SQLite-oriented. PostgreSQL migrations and cross-host operational validation remain a separate production milestone.
