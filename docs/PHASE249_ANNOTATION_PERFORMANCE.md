# Phase 249 — Annotation performance analytics

## Scope

This phase adds an authenticated, owner-only, read-only performance report for annotation projects. It aggregates evidence already persisted by assignment, gold-calibration, reviewer, and adjudication workflows. It does not score workers with an opaque composite ranking or alter task routing.

## Metrics

### Annotators
- Assignment counts by persisted state: assigned, in progress, submitted, revoked.
- Gold-standard sample count, correct count, observed accuracy, and calibration state using the same policy as reviewer calibration (minimum 5 results and at least 80% exact-label accuracy).
- Number of rework decisions attributed to the source assignment IDs in the adjudication event history.
- Median elapsed seconds from assignment creation to submission, where timestamps are valid and non-negative.

### Reviewers
- Accepted and rework decisions on ordinary annotation submissions, grouped by persisted reviewer identity.
- Independent adjudication count, accepted adjudications, and rework adjudications from append-only adjudication events.
- Gold-standard sample count, observed accuracy, and calibration state when gold evidence exists for the reviewer identity.

## API

- `GET /v1/annotation/projects/{project_id}/performance-report`
- Requires an authenticated project owner.
- Returns per-annotator and per-reviewer aggregates, plus measurement definitions and limitations.
- Returns 404 for projects the authenticated user does not own.
- Does not expose annotation payloads, individual gold answers, or other projects' records.

## Measurement safeguards

- Calibration is marked `insufficient_evidence` below five gold results; accuracy is not presented as a pass/fail conclusion at that sample size.
- The report uses explicit sample counts alongside observed accuracy.
- Elapsed time is assignment creation-to-submission wall-clock time, not active work time or a productivity measure.
- Rework attribution uses the exact source assignment IDs captured in the immutable adjudication event, not current task state.
- Metrics are descriptive and must not be treated as a worker ranking or standalone employment decision.
- The report is computed from persisted project-scoped evidence; no external analytics service or background tracking is introduced.

## Limitations

- No time-series drift detection, confidence intervals, statistical significance testing, or configurable calibration thresholds yet.
- No cross-project aggregation; project owners see only their own project.
- Reviewer quality is currently represented by adjudication counts and available gold calibration evidence; a dedicated blinded reviewer benchmark workflow remains future work.
- Ordinary submission timing does not represent active work time.
