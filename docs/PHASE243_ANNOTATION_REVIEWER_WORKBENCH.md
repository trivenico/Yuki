# Phase 243 — Annotation reviewer workbench and independent access

## Delivered

- Adds a durable per-project reviewer grant registry in the annotation SQLite store.
- Project owners can grant and revoke reviewer access for existing Yuki accounts. Granting requires an authenticated owner session and an existing account ID; project owners cannot assign themselves as a reviewer.
- Reviewer assignments are owner/project scoped, survive process restarts, can be revoked, and are represented in the audit trail without including annotation payloads.
- Adds reviewer queue reads that include the task input, project instructions, rubric and submitted annotation payload.
- Adds reviewer decisions for accept or rework, reviewer notes, and a persisted reviewer identity on each submission.
- Adds owner-side reviewer assignment controls and a reviewer queue panel with accept/rework actions.
- Adds an optional external `annotation_reviewer_authorizer(user, project_id) -> owner_id | None` integration for deployments with an existing centralized membership or policy service. When supplied, it is the authoritative reviewer resolver; otherwise durable project grants are used.
- Adds schema migration for the new `reviewer_id` submission column and an index for project review queue reads.

## API contract

- `POST /v1/annotation/projects/{project_id}/reviewers` accepts exactly `{ "reviewer_user_id": "..." }` and grants an existing account reviewer access.
- `GET /v1/annotation/projects/{project_id}/reviewers` lists active reviewer IDs and grant timestamps for the project owner.
- `DELETE /v1/annotation/projects/{project_id}/reviewers` revokes the specified active grant.
- `GET /v1/annotation/projects/{project_id}/review-queue` returns only submitted items the authenticated reviewer is authorized to access, including task and rubric context.
- `POST /v1/annotation/submissions/{submission_id}/review` requires `decision`, `project_id`, and optional `notes`. The decision is either `accepted` or `rework`.
- Reviewer identity comes from the authenticated session, never the request body. The API binds every decision to the project authorized for that reviewer.

## Security and concurrency

- Without an external policy adapter, reviewer access is checked against the persisted owner-managed grant.
- The built-in queue read checks the active grant and reads queue rows in the same SQLite read transaction. Review decisions recheck the grant inside the same immediate write transaction that updates the submission and task, so a concurrent revocation cannot race through the authorization boundary.
- A reviewer cannot review a submission owned by their own account. Reviewer identity is stored separately from the project/task owner and included in audit metadata.
- Submission and task state transitions must both succeed; otherwise the transaction rolls back. A second reviewer cannot silently overwrite a completed review.
- Cross-project submission IDs cannot be used with a different authorized project ID.
- Reviewer grants are revocable; revoked reviewers receive 403 and cannot read queue data or make new decisions.
- Queue and reviewer UI rendering use DOM text nodes and `textContent`; annotation JSON is not inserted as HTML.

## Operational boundaries

- Reviewer assignment currently uses account IDs; a searchable user picker and richer organization/team membership UI are not included.
- The optional external authorizer is trusted application code. If configured, it supersedes the built-in grant resolver and must itself enforce project membership and revocation.
- This work does not add collaborative editing, consensus review, reviewer calibration analytics, marketplace delivery, or payment handling.
- The reviewer workbench remains local to Yuki; it does not send decisions or annotations to external platforms.

## Validation

- Added tests for grant persistence, revocation, self-review prevention, queue context, default-deny behavior, project binding, reviewer identity, and runtime authorization wiring.
- Browser JavaScript syntax and static UI element-reference checks are performed during engineering review.
- GitHub Actions on the exact PR head remains the authoritative Python/test validation; no local Python test run is claimed.
