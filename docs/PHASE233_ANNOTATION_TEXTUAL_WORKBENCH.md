# Phase 233 — Textual Annotation Workbench

## Scope

Adds a real optional terminal client over the existing `SQLiteAnnotationWorkbench`.
It lists owner-scoped projects, opens project instructions/rubrics, browses tasks,
claims tasks with optimistic versions, submits typed JSON annotations, inspects
submission metadata, and reviews submissions.

## Safety and operational boundaries

- The UI receives an already constructed workbench and owner ID; it does not
  invent an identity, open a database path, or bypass application composition.
- Claims and submissions are explicit commands. There is no auto-claim, auto-save,
  background work, model invocation, or marketplace/client delivery.
- Submission requires the literal `CONFIRM`, sets `human_confirmed=True`, and
  records `ai_assisted=False`. The backend remains authoritative for task-family
  schema/rubric validation and optimistic version checks.
- A stale version is reported as a conflict and the user is told to reload the task.
- The UI displays opaque asset references only. It does not preview or download
  assets; quarantined storage remains inaccessible until server-side scanning.
- Textual is optional. Install with `pip install -r requirements-ui.txt`.

## Deliberate limitations

This phase supplies a functional command-driven TUI, not graphical polygon/video
timeline/audio waveform editors. The UI is not yet wired into the CLI/dashboard
composition root because the local CLI runtime does not currently construct an
annotation store or define its database lifecycle. The next integration phase
must add explicit runtime configuration, store lifecycle/close semantics, and
task-family-aware form editors without duplicating backend validation.
