# Phase 245 — Independent annotation assignments and agreement

## Delivered

- Adds owner-created independent assignments for the same text-classification task to 2–20 distinct Yuki accounts.
- Adds per-assignment claim, optimistic versioning, human-confirmed submission, quality assessment, and owner revocation before submission.
- Adds an authenticated worker inbox that returns only the signed-in annotator's assignments, task input, project instructions, and rubric.
- Adds owner-scoped agreement reporting with mean task-level pairwise label agreement and Fleiss' kappa when the evaluated tasks have a consistent number of ratings.
- Adds workbench controls for creating assignments, loading a worker's own assignments, submitting a label, and viewing agreement metrics.
- Records assignment creation, submission, and revocation in the existing owner audit trail.

## Supported scope

Independent agreement is deliberately restricted to `text_classification` in this phase. Image-classification assignments are not enabled yet because the existing safe asset-preview endpoint authorizes the project owner, not delegated annotators. Extending image work requires a separate asset-access capability bound to an active assignment; this phase does not weaken the existing asset authorization boundary.

The agreement metric is exact label agreement for classification labels. It is not a semantic similarity score and is not a substitute for reviewer adjudication. Fleiss' kappa is returned only when at least one task has two or more submitted ratings and every evaluated task has the same number of ratings. If the expected statistical denominator is degenerate (for example, every rating has the same category across the entire sample), kappa is `null`, not a fabricated numeric value. The report includes the evaluated task count and mean pairwise agreement independently.

## API

- `POST /v1/annotation/projects/{project_id}/tasks/{task_id}/independent-assignments` — project owner assigns 2–20 existing accounts.
- `DELETE /v1/annotation/projects/{project_id}/independent-assignments/{assignment_id}` — project owner revokes an assignment that has not been submitted.
- `GET /v1/annotation/assignments` — signed-in annotator lists their own assignments.
- `POST /v1/annotation/assignments/{assignment_id}/claim` — annotator claims/resumes with optimistic version.
- `POST /v1/annotation/assignments/{assignment_id}/submit` — annotator submits a human-confirmed classification label.
- `GET /v1/annotation/projects/{project_id}/agreement-report` — project owner reads aggregate agreement metrics.

## Security and correctness

- Assignment creation is owner/project/task scoped and validates every account against the auth service before persistence.
- An owner cannot assign themselves; duplicate annotators and duplicate task/annotator assignments are rejected.
- Worker reads and writes are scoped by the authenticated annotator identity; assignment IDs alone do not grant access.
- Assignment transitions use SQLite immediate write transactions and optimistic versions.
- Submission revalidates the project rubric and canonical task-family payload validator before persisting the result.
- Owner revocation and worker submission serialize through SQLite write transactions; submitted assignments cannot be revoked.
- Agreement reports expose aggregate metrics only and never disclose annotator identities or individual labels.
- Assignment data is kept separate from the existing single-worker task lifecycle and accepted-work export until multi-rater adjudication is explicitly designed.

## Deliberate limitations

- No blind gold-standard task injection or gold answer storage is included.
- No reviewer calibration scoring is included; this phase establishes the independent labels and agreement evidence needed for that work.
- No majority-vote or automatic consensus decision is applied.
- No image/audio/video independent assignments are exposed until delegated asset access and modality-specific agreement metrics are implemented.
- Assignment delivery remains inside Yuki; this does not submit work to external marketplaces or clients.

## Validation

- Adds core tests for private worker inboxes, assignment submission, duplicate/self-assignment rejection, owner revocation, owner isolation, and agreement calculations.
- Adds authenticated API tests for assignment creation, worker claim/submission, cross-user access denial, and owner-only agreement reporting.
- Browser JavaScript syntax and static DOM references are checked before opening the pull request.
- GitHub Actions on the exact PR head remains authoritative for Python and cross-platform integration tests. No local Python test run is claimed.
