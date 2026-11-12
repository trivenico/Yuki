# Phase 248 — Rework lifecycle and immutable dataset manifests

## Delivered

- Versioned independent-assignment rounds. Existing assignments and submitted payloads are retained; a rework round creates new assignment IDs and records a higher round number.
- Rework can be reopened only after the current adjudication is `rework`. The task is returned to `pending` as the new round is issued.
- A rework round requires 2–20 distinct annotators who have not previously worked on that task. This prevents the existing per-task/per-annotator uniqueness constraint from forcing historical assignment mutation.
- Adjudication evidence and decisions are scoped to the current assignment round.
- Every adjudication is copied to an append-only event history with the round, reviewer, decision, final label, notes, source assignment IDs, agreement, and timestamp. The existing adjudication row remains the current decision pointer for compatibility with Phase 247 exports.
- Owner-created delivery manifests snapshot accepted single-submission and adjudicated work in one SQLite write transaction.
- Manifest snapshots use canonical JSON serialization and SHA-256 checksums. They include task IDs, source provenance, accepted labels/payloads, quality evidence, AI-assistance status, dataset version, and creation time.
- Manifest idempotency keys prevent accidental repeat creation on retry. Manifest verification recomputes the checksum from the stored snapshot and reports tampering.
- Manifests are owner/project isolated and creation is audited.

## API

- `POST /v1/annotation/projects/{project_id}/tasks/{task_id}/rework-assignments` — owner creates a new assignment round using 2–20 new annotator account IDs.
- `POST /v1/annotation/projects/{project_id}/delivery-manifests` with `{"idempotency_key":"..."}` — create or retrieve the same manifest for an idempotent retry.
- `GET /v1/annotation/projects/{project_id}/delivery-manifests` — list compact owner manifest summaries.
- `GET /v1/annotation/projects/{project_id}/delivery-manifests/{manifest_id}` — retrieve a single immutable snapshot.
- `GET /v1/annotation/projects/{project_id}/delivery-manifests/{manifest_id}/verify` — recompute and compare the SHA-256 checksum.

## Integrity and lifecycle rules

- Prior assignments and their submitted payloads are never rewritten by rework.
- Rework requires a prior persisted rework adjudication; it cannot reopen accepted/completed work.
- Current-round labels alone are used for the next adjudication.
- Prior adjudications are retained in `annotation_adjudication_events`; the compatibility adjudication row represents the latest decision.
- A manifest is a point-in-time snapshot. Later task changes or later manifests do not alter its stored snapshot.
- Manifest creation is bounded to 5,000 items and 16 MiB of canonical snapshot data; it fails rather than silently truncating a dataset. Identical snapshot digests are deduplicated per owner/project, even when a retry uses a different idempotency key.
- Verification detects snapshot changes; it is an integrity check, not a signature or proof against an attacker who can rewrite both the snapshot and checksum in the database.

## Deliberate limitations

- Rework currently requires new annotators for the task; returning previous annotators is rejected rather than mutating historical assignment records.
- This phase does not automatically deliver manifests to an external client or marketplace. Export remains owner-controlled.
- Dataset manifests are stored in SQLite and returned through the authenticated API; object-storage publication, signed attestations, retention policy, and large-dataset streaming remain separate work.
- The manifest checksum is an integrity digest, not a cryptographic signature. External verification needs a separately protected signing key or trusted digest channel.
- Assignment and adjudication remain limited to the text-classification independent-assignment workflow.

## Validation

- Added core regression coverage for rework rounds, historical assignment retention, append-only adjudication events, manifest idempotency, owner isolation, and checksum tampering.
- GitHub Actions on the exact PR head remains authoritative for Python and cross-platform validation. No local Python test run is claimed.
