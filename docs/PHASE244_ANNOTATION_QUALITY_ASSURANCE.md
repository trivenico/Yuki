# Phase 244 — Deterministic annotation quality assurance

## Delivered

- Adds a pure, deterministic quality assessment engine in `core/annotation_quality.py`.
- Adds a versioned, optional project rubric `quality` policy with validated configuration:
  - `minimum_confidence` for tasks that include a numeric confidence field.
  - `require_rationale` for task payloads that support a rationale field.
  - `minimum_items` and `maximum_items` for task-family annotation counts.
  - `required_labels` for task families with label-bearing annotation items.
- Produces stable machine-readable findings with severity, code, and safe summary text; also reports annotation item count and distinct labels.
- Runs quality assessment only after the canonical task-family payload validator succeeds.
- Persists the quality result in the same SQLite transaction as the submission and task state transition.
- Migrates existing submission databases by adding a non-null `quality_json` column with an empty-object default. Historical submissions are counted as `unknown`, never retroactively marked as passed.
- Adds an owner-scoped project quality report with submission counts, pass/warning/unknown counts, review outcomes, finding counts, annotation item totals, and a pass rate.
- Includes the persisted quality assessment in accepted-work exports so downstream owner-controlled delivery retains the QA evidence.
- Adds an authenticated API endpoint and a project-level quality panel in the annotation workbench.

## Policy and result contract

The project rubric may include:

```json
{
  "labels": ["positive", "negative"],
  "quality": {
    "minimum_confidence": 0.8,
    "require_rationale": true,
    "minimum_items": 1,
    "maximum_items": 20,
    "required_labels": ["positive"]
  }
}
```

Unknown quality-policy keys, invalid numeric ranges, duplicate required labels, and inconsistent item bounds are rejected at project creation. Quality policy validation is also applied before each submission assessment.

A submission receives `quality.status` of `pass` or `warning`, a list of deterministic findings, metrics, and policy version. Quality findings do not mutate the annotation payload and do not change submission review status.

## API

- `GET /v1/annotation/projects/{project_id}/quality-report` returns the authenticated owner's aggregate quality report.
- Submission responses include the persisted `quality` assessment.
- Accepted exports include the same assessment next to the annotation and human review timestamp.
- Reviewer queue entries include the same assessment so reviewers can consider deterministic warnings alongside the payload, task input, and rubric.

## Security, correctness, and operational behavior

- The report is owner-scoped; a different owner receives the same not-found response as for an inaccessible project.
- The report contains aggregate metrics and finding codes only; it does not expose annotation payloads, task inputs, or reviewer notes.
- Quality assessment and submission persistence share one transaction, so a failed submission cannot leave a detached assessment.
- Quality checks are bounded by the existing rubric and payload size limits.
- The engine is deterministic and has no model, network, marketplace, or external side effects.
- A `pass` means only that the configured deterministic checks passed. It is not a claim that the annotation is semantically correct.
- Automated findings never accept work, reject work, or replace reviewer judgment.

## Deliberate limitations

- Inter-annotator agreement is not yet calculated. The current task workflow assigns a single active worker to a task and does not provide independent parallel annotations suitable for agreement statistics.
- Gold-standard task injection and reviewer calibration are not included; they require explicit gold-set ownership, blinding, and leakage controls before they can be safely used for worker scoring.
- Duplicate detection is not enabled generically because equal payloads can be valid for different inputs (for example, repeated classification labels). Future detection must be task-family-aware and avoid false positives.
- Confidence thresholds are advisory and are applied only when a supported confidence value is present.
- This phase does not deliver annotations to marketplaces or client systems, and does not create earnings or payment records.

## Validation

- Adds unit coverage for policy validation, confidence/rationale findings, item-count checks, required labels, clean results, and payload immutability.
- Adds persistence, migration, aggregation, API owner-isolation, and served-UI regression tests.
- Browser JavaScript syntax and DOM ID references are statically checked.
- GitHub Actions on the exact PR head remains authoritative for Python and integration tests. No local Python test run is claimed.
