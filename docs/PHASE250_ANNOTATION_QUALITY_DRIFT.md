# Phase 250 — Annotation quality drift detection

## Scope

Phase 250 adds an owner-scoped, auditable statistical assessment of blinded gold-standard accuracy for text-classification annotation projects. It complements Phase 249's descriptive performance report without treating ordinary annotation labels as ground truth.

## Statistical policy

- Baseline window: 90 days ending 30 days before assessment.
- Recent window: the 30 days immediately before assessment.
- Minimum evidence: 20 baseline gold results and 10 recent gold results per annotator.
- Effect-size gate: baseline accuracy must exceed recent accuracy by at least 10 percentage points.
- Significance gate: two-sided pooled two-proportion z-test with p ≤ 0.01.
- Each window reports sample count, correct count, accuracy, and a Wilson 95% confidence interval.
- An annotator is classified as `insufficient_evidence`, `stable`, or `drift_detected`.

## Persistence and API

Reports are immutable SQLite snapshots containing their policy, evidence summaries, results, limitations, and generation time. SHA-256 detects accidental or unauthorized snapshot modification; it is not a digital signature. Idempotency keys make POST retries safe. Audit events record report creation without copying annotation payloads.

- `POST /v1/annotation/projects/{project_id}/quality-drift-reports` with `{"idempotency_key":"..." }`
- `GET /v1/annotation/projects/{project_id}/quality-drift-reports` for compact owner-only history
- `GET /v1/annotation/projects/{project_id}/quality-drift-reports/{report_id}` for a full owner-only report

## Safety boundaries

- Gold results only; ordinary submissions are not assumed to have known truth.
- Minimum sample counts, effect-size threshold, and significance threshold must all pass before an alert is emitted.
- No automatic suspension, reassignment, pay adjustment, ranking, or external action.
- Reports are project-owner scoped and append-only.
- Fixed rolling windows do not model seasonality, task-difficulty changes, or multiple-comparison correction. Alert outcomes require human review.
- This phase supports text classification only.

## Validation

Core tests exercise a synthetic, deliberately large accuracy drop, idempotent creation, integrity metadata, and owner isolation. API tests cover authenticated creation, retrieval, history, and cross-owner denial. Exact-head CI remains the validation authority; no local test run is claimed.
