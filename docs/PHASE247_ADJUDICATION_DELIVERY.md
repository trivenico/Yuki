# Phase 247 — Calibrated adjudication and quality-gated delivery

## Delivered

- Owner-scoped independent-submission evidence retrieval for a selected task.
- Durable adjudication records that preserve every original annotator assignment and submitted label.
- Owner adjudication and delegated reviewer adjudication.
- Delegated reviewers must have an active project reviewer grant when the built-in authorizer is used, and must meet the project's current calibration evidence threshold.
- Accepted decisions require a final rubric-valid label.
- Disagreement decisions require explanatory notes.
- Rework decisions are persisted separately and do not enter accepted delivery exports.
- Accepted independent adjudications are included in the existing accepted-work export pipeline.
- Owner delivery reports summarize accepted, rework, pending adjudication, and total accepted delivery counts.
- Workbench UI supports inspecting submitted labels, adjudicating a task, and refreshing delivery metrics.

## Reviewer eligibility

A delegated reviewer must have at least five submitted gold-standard evaluations and exact-label accuracy of at least 80%. The threshold is enforced inside the same SQLite immediate transaction that records the adjudication. A project owner may adjudicate directly. External reviewer authorization callbacks remain supported, but they do not bypass calibration.

Calibration is a quality signal, not a guarantee of future accuracy or a substitute for project-specific human oversight.

## Adjudication behavior

- At least two submitted independent assignments are required.
- Only submitted assignments are considered; unsubmitted or revoked assignments are excluded.
- The adjudication records the exact source assignment IDs, reviewer identity, decision, final label, agreement ratio, notes, and timestamp.
- If submitted labels disagree, an accepted decision requires notes explaining the resolution.
- A task can be adjudicated only once; duplicate adjudication is rejected.
- Original independent assignment payloads are immutable through this workflow.
- The task state is advanced to `accepted` or `rework` transactionally with the adjudication record.

## Delivery boundary

The accepted export now includes:
1. Existing single-submission work explicitly accepted through the existing reviewer workflow.
2. Independent tasks with an accepted adjudication and a final label.

Rework adjudications are excluded. Independent submissions are not exported merely because they were submitted or because annotators agree. The final decision must be explicitly accepted. Exported independent work is marked with quality provenance `source=independent_adjudication`, agreement ratio, reviewer identity, and a human-reviewed `ai_assisted=false` value.

## API

- `GET /v1/annotation/projects/{project_id}/tasks/{task_id}/independent-submissions` — owner-only evidence retrieval.
- `POST /v1/annotation/projects/{project_id}/tasks/{task_id}/adjudication` — owner or authorized calibrated reviewer adjudicates.
- `GET /v1/annotation/projects/{project_id}/delivery-report` — owner-only delivery metrics.
- Existing accepted export behavior is extended to include accepted independent adjudications.

## Security and correctness

- Every operation is owner/project/task scoped.
- Built-in reviewer grants are rechecked inside the write transaction to close revoke/adjudicate races.
- Reviewer calibration is checked from persisted gold results in the adjudication transaction.
- The adjudication write and task-state transition are atomic.
- Duplicate adjudications and task-state races fail closed.
- Annotator identities and labels are returned only to the project owner through the evidence endpoint.
- API tests cover cross-owner denial and owner-only delivery reports.

## Deliberate limitations

- Adjudication currently supports text-classification independent assignments only.
- A rework decision is recorded and excluded from delivery; this phase does not yet reopen or reissue independent assignments for that task.
- Consensus is reported as the largest-label share; no automatic majority decision is made.
- Reviewer eligibility uses the current fixed five-gold-task / 80% policy; project-level policy administration is not exposed.
- Export remains an owner-invoked read pipeline; this phase does not deliver data to an external client or marketplace.

## Validation

- Added core tests for calibration-gated reviewer eligibility, disagreement notes, immutable source evidence, accepted export inclusion, and owner isolation.
- Added authenticated API tests for owner adjudication, delivery reporting, and cross-owner denial.
- Browser JavaScript syntax checked after UI wiring; exact DOM-reference validation is performed before PR submission.
- GitHub Actions on the exact PR head remains authoritative for Python and cross-platform validation. No local Python test run is claimed.
