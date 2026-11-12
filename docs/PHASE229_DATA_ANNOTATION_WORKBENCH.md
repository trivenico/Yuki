# Phase 229 — Yuki Data Annotation Workbench

## Objective

Provide an owner-scoped workbench for legitimate data annotation and AI evaluation
projects. The first delivery focuses on safe project/task lifecycle, human-reviewed
submissions, batch intake and accepted-only export. It is not a marketplace bot and
does not claim that any paid work has been obtained.

## Supported task families

- Image classification and object detection/segmentation task definitions.
- Video tracking task definitions.
- Audio transcription and speaker diarization task definitions.
- Text classification and entity extraction.
- LLM response ranking and rubric-based evaluation.
- Custom projects with owner-supplied instructions and rubric.

The workbench stores task inputs as bounded JSON and media as opaque asset references.
It does not fetch arbitrary remote URLs. A future asset service must enforce content
type/size limits, malware scanning where applicable, tenant isolation, signed access,
retention/deletion policies and protection against sensitive-data leakage.

## Implemented lifecycle

1. Create an owner-scoped project with task family, instructions and rubric.
2. Add individual tasks or atomically import a batch of up to 100 tasks.
3. Claim tasks with optimistic version checks.
4. Submit a structured annotation only after explicit human confirmation.
5. Review a submission as accepted or rework.
6. Export only accepted annotations, scoped to the authenticated owner/project.
7. Inspect a metadata-only audit trail; raw annotation content is not copied into audit events.

Project/task/submission ownership is derived from the authenticated bearer session.
Request bodies cannot supply an owner ID. API responses are marked no-store.
Database writes use SQLite transactions, foreign keys, WAL and a private POSIX file
mode. Opaque asset references are accepted; arbitrary URLs and filesystem paths are not.

## API surface

- POST /v1/annotation/projects — create a project.
- GET /v1/annotation/projects — list the current owner's projects.
- GET /v1/annotation/projects/{project_id} — read an owned project.
- POST /v1/annotation/projects/{project_id}/tasks — create one task.
- POST /v1/annotation/projects/{project_id}/tasks/batch — atomically import 1–100 tasks.
- GET /v1/annotation/projects/{project_id}/tasks — list owned project tasks.
- GET /v1/annotation/projects/{project_id}/export — export accepted annotations only.
- POST /v1/annotation/tasks/{task_id}/claim — claim using expected_version.
- POST /v1/annotation/tasks/{task_id}/submit — submit using expected_version and human_confirmed: true.
- GET /v1/annotation/tasks/{task_id}/submissions — list submission history.
- POST /v1/annotation/submissions/{submission_id}/review — accept or request rework.

The runtime mounts these endpoints only when a SQLiteAnnotationWorkbench instance
is explicitly supplied as annotation_workbench to build_conversation_runtime.

## AI assistance and work integrity

The service records whether a submission was AI-assisted, but AI output is not a
submission by itself. human_confirmed must be explicitly true. The current phase
does not yet invoke a model, create a separate AI-draft record, validate each task
family's geometry/audio/text schema, or integrate a client-specific grading rubric.
Those are required follow-up phases. Model suggestions must remain visibly separate
from final annotations, and all deliverables must be reviewed against the client's
actual instructions.

Yuki must not impersonate a human annotator, falsify qualification results, fabricate
time/work logs, bypass platform restrictions, or submit external work without owner
approval. Marketplace/client integrations must use approved APIs and respect their
terms, rate limits, confidentiality, data-processing and retention requirements.

## Not implemented yet

- Secure asset upload, preview, streaming and deletion service.
- Task-family-specific typed validators and annotation editors.
- AI draft generation, uncertainty scoring and disagreement detection.
- Calibration tasks, gold-label sets, inter-annotator agreement and quality metrics.
- CSV/JSONL import/export profiles matching specific client formats.
- Marketplace discovery/eligibility/payment research and OAuth connectors.
- Client submission adapter with explicit owner approval and idempotent delivery.
- Earnings reconciliation against actual platform statements; estimates must never be
  reported as realized income.
- Desktop annotation UI and keyboard-first review workflow.

## Release acceptance

- Unit and API tests cover owner isolation, human confirmation, stale writes, task
  batch atomicity, review transitions and accepted-only export.
- Cross-platform CI must pass before merge.
- Security review must verify database path handling, payload limits, auth behavior,
  data retention and asset-storage design before enabling real client data.
- No claim of paid-project readiness until asset handling, task-specific validation,
  UI review, external delivery controls and platform policy review are complete.
