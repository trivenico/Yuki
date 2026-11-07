# Phase 264 — Artifact provenance and verification workflow

## Delivered

Added `automation/verification_workflow.py`.

The workflow:
- Records exact artifact bytes with SHA-256, byte length, plan/subtask/task identity,
  and author agent ID.
- Derives a deterministic artifact ID from plan, subtask, task, and content digest.
- Persists artifact records in SQLite.
- Links stored independent verification reports to artifacts.
- Requires an authorized reviewer actor for report linking.
- Rejects report/artifact digest mismatches and author mismatches.
- Rejects reports whose reviewer agent IDs include the artifact author.
- Exposes a read-only status snapshot showing linked reports and whether any
  linked report has an accepted decision.

Added `tests/test_verification_workflow.py` for successful linking, digest
mismatch, reviewer authorization, self-review rejection, and failed-report
handling.

## Critical limitations

This module does not automatically extract artifact bytes from task results and
is not wired into `SpecialistAgentCoordinator.advance()`. Coordinator task
state remains `succeeded` when a worker finishes; that means execution completed,
not that engineering work has been independently accepted.

The caller must explicitly record the exact artifact bytes and link an evidence
report. Reviewer actor authorization is supplied as a callback, but mapping
reviewer agent identities to authenticated actors/roles remains an integration
responsibility. Evidence references are not independently fetched or validated
by this workflow.

The next integration must make coordinator acceptance a distinct state and
require a verified artifact/report link before downstream release. Tests have
been authored but not executed here; no CI result is claimed.
