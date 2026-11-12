# Phase 252 — Blinded Calibration Requalification

## Scope

This phase adds owner-controlled calibration sessions over already registered gold-standard text-classification tasks. A session assigns every selected gold task to each selected annotator, with assignment creation and session persistence performed in one SQLite `BEGIN IMMEDIATE` transaction.

## API

- `POST /v1/annotation/projects/{project_id}/calibration-sessions`
  - Body: `task_ids`, `annotator_user_ids`, and `idempotency_key`.
  - Requires an active text-classification project, registered gold tasks, at least the configured minimum number of tasks, known annotator accounts, and previously unused annotator/task pairs.
  - Replays with the same idempotency key and identical request return the original session. Reusing the key with different inputs is rejected.
- `GET /v1/annotation/projects/{project_id}/calibration-sessions`
  - Returns session status and assignment/submission counts only; never expected labels.

## Blinding and evidence

Expected labels remain exclusively in `annotation_gold_standards`. Worker inbox projections continue to return task input, project instructions, and rubric—not gold records. Gold scoring happens inside the submission transaction and stores the result linked to the calibration session.

When a project has a calibration session, the calibration report and reviewer eligibility check use results from the newest session rather than mixing historical evidence into the fresh round. Projects without sessions retain their existing historical calibration behavior.

## Safety and operational boundaries

- Owner-scoped project lookup and authenticated account validation precede assignment creation.
- Session and assignments are created atomically; SQLite uniqueness and transaction locking prevent duplicate annotator/task assignments and concurrent partial sessions.
- No worker suspension, pay change, task routing, or external platform action is performed.
- Existing historical assignments and gold results are preserved.
- The current implementation supports text-classification gold standards only.

## Validation

Automated tests cover idempotent creation, gold-label blinding in worker inbox projections, session completion, latest-session calibration evidence, unregistered gold rejection, reused annotator/task rejection, owner isolation, and idempotency conflicts. CI results must be checked against the exact pull-request head before declaring the phase validated.
