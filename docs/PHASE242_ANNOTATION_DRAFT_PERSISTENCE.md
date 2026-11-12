# Phase 242 — Durable annotation drafts and recovery

## Delivered

- Adds an owner- and task-scoped `annotation_drafts` SQLite table with a foreign key to the task record, bounded JSON payloads (64 KiB), monotonic per-draft revisions, and timestamps.
- Adds authenticated `GET`, `PUT`, and `DELETE /v1/annotation/tasks/{task_id}/draft` routes. The owner is always derived from the authenticated session; request-supplied owner IDs are not accepted.
- Draft writes require an active project and an editable `in_progress` or `rework` task. The expected revision is mandatory: zero creates the first revision, and subsequent writes must match the current revision.
- Stale writes return HTTP 409 and do not overwrite the current draft. Draft operations do not increment the task's workflow version.
- A successful submission deletes the current draft in the same SQLite transaction that creates the submission and advances the task.
- Audit events record draft save/delete operations and revision numbers, never the annotation content.
- The web workbench autosaves after a short idle interval, flushes on visibility changes and before task/project changes or submission, and restores a saved draft when reopening an in-progress task.
- Per-task queued saves prevent overlapping writes from racing within the current browser session. On a revision conflict the UI stops autosaving and asks the user to refresh instead of overwriting another session's changes.
- Adds bounded undo/redo history for the current annotation editing session and persists the history with the draft where it fits within the same storage limit.

## API contract

- `GET /v1/annotation/tasks/{task_id}/draft` returns `{ "draft": null }` when no draft exists for that authenticated owner/task pair.
- `PUT /v1/annotation/tasks/{task_id}/draft` accepts exactly `{ "payload": object, "expected_revision": integer }` and returns the persisted draft including its new revision.
- `DELETE /v1/annotation/tasks/{task_id}/draft` accepts exactly `{ "expected_revision": integer }`; deletion is compare-and-swap protected.
- Cross-owner reads are indistinguishable from missing drafts. No draft payload is included in audit events.

## Recovery and concurrency

- Drafts are durable in the configured workbench SQLite database; they survive browser refreshes and application restarts.
- The UI keeps a dirty payload in memory during transient save failures and retries. It blocks submission and task switching if the latest draft cannot be saved.
- Revision conflicts are not automatically merged. A user must refresh/reopen the task to load the newer persisted draft.
- The database uses the existing workbench WAL, busy timeout, foreign-key enforcement, and private-file permission behavior.

## Boundaries and limitations

- This does not implement multi-user collaborative editing or automatic three-way merge.
- Browser termination during the debounce interval can still lose the very latest keystrokes; visibility/pagehide flushes are best-effort. The UI reports successful saves with a revision number.
- Undo/redo history is bounded and stored with the draft only while the combined payload remains within 64 KiB; oldest history entries are discarded first.
- Drafts are not sent to marketplaces or external clients. Submission still requires the separate explicit human confirmation flow.
- Local test execution is not claimed; the exact PR head's GitHub Actions results are authoritative.
