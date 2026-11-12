# Phase 246 — Blinded gold-standard calibration

## Delivered

- Project owners can register a pending text-classification task as a gold standard with an expected rubric label.
- Expected answers are stored in a dedicated owner-scoped table and are never returned by annotator inbox, assignment creation, claim, or submission responses.
- Gold registration must precede independent assignment and any existing submission; task must still be pending.
- Annotator submissions for gold tasks are scored transactionally against the protected expected label.
- Owner-only calibration reports show each annotator's evaluated gold count, correct count, accuracy, and calibration state.
- Workbench UI supports gold registration for the selected task and calibration report refresh.

## Calibration policy

The initial policy is intentionally explicit and conservative:
- Minimum evidence: 5 submitted gold tasks per annotator.
- Passing threshold: 80% exact-label accuracy.
- Fewer than 5 evaluated gold tasks: `insufficient_evidence`.
- At least 5 tasks and accuracy >= 80%: `calibrated`.
- At least 5 tasks and accuracy < 80%: `needs_review`.

The core report method accepts validated policy overrides for future controlled configuration, but the public API currently uses the defaults. Accuracy is exact rubric-label equality, not semantic or fuzzy similarity. The status is a calibration signal, not an automatic employment, payment, or external-platform eligibility decision.

## API

- `POST /v1/annotation/projects/{project_id}/tasks/{task_id}/gold-standard` — owner registers `expected_label`; response omits the answer.
- `GET /v1/annotation/projects/{project_id}/calibration-report` — owner reads aggregate gold coverage and per-annotator calibration evidence.

Gold tasks continue through the existing independent assignment lifecycle. Gold answer records and correctness results are separate from annotator-visible assignment records.

## Security and correctness

- Gold creation requires project ownership, an active text-classification project, a pending task, a rubric-valid label, and no existing assignments or submissions for that task.
- Duplicate gold registration is rejected.
- The expected answer is not added to task input, rubric, assignment metadata, or worker-facing responses.
- Gold correctness is computed inside the same SQLite immediate transaction as assignment submission and stored separately.
- Worker access remains scoped to the authenticated annotator identity; calibration reporting is owner-scoped.
- Calibration report exposes annotator identifiers and aggregate scores only to the project owner.
- No gold answer is returned by any worker endpoint.

## Deliberate limitations

- Gold-standard support is text classification only.
- Gold tasks must be manually authored and validated by the project owner; there is no external ground-truth verification.
- No adaptive or hidden random gold injection, answer rotation, challenge-set leakage analysis, temporal decay, or reviewer adjudication is included.
- The initial policy uses a five-task minimum and 80% threshold; the API does not yet expose per-project calibration policy editing.
- A calibrated status does not automatically grant reviewer privileges, accept submissions, or authorize external marketplace actions.

## Validation

- Core tests cover blinded assignment payloads, exact correctness scoring, threshold states, owner isolation, rubric validation, and ordering safeguards.
- API tests cover owner-only gold registration, omission of expected answers, worker submission, and owner-only calibration reports.
- Browser JavaScript syntax and static DOM reference checks are run before PR creation.
- GitHub Actions on the exact PR head remains authoritative for Python, security, and cross-platform validation. No local Python test run is claimed.
