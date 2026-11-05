# Phase 187 — Bind specialist execution identity to the plan

Each planned subtask now stores a SHA-256 fingerprint of the specialist's agent ID, action, capabilities, and permissions. Immediately before dispatch, the coordinator recomputes the fingerprint from the refreshed registry. A missing or changed identity blocks the subtask and records `subtask.agent_identity_changed`; no runtime task is created.

Existing databases gain an `agent_identity_digest` column. Historical pending subtasks without a fingerprint fail closed rather than dispatching under an unverified identity. Already dispatched tasks are not rewritten.

This binds the registered action identifier and declared authority, but is not a cryptographic signature of executable code and does not sandbox a worker. Workflow engine and tool adapters must continue enforcing permissions at execution time.

Regression coverage changes the persisted specialist action after planning and verifies the task is blocked with no runtime task created.
