# Phase 231 — Quarantined annotation asset storage

## Scope

This phase adds a local storage primitive for annotation image/audio/video assets. It is intentionally not yet exposed as an upload or preview API. The annotation API must not accept client-provided storage paths, arbitrary URLs, or caller-controlled scan status.

## Security properties

- Owner and project scope are mandatory on every operation.
- Asset IDs are random opaque 128-bit identifiers; storage paths are derived from a SHA-256 owner directory and the validated asset ID.
- New uploads are quarantined and cannot be read until trusted server-side scanning marks them clean.
- Media types are detected from a narrow set of binary signatures; caller MIME headers and file extensions are not trusted.
- Upload size is bounded (25 MiB by default, configurable up to 250 MiB).
- Files are created exclusively with restrictive permissions and no-follow flags where the platform supports them; storage root and database symlinks are rejected.
- Reads are owner/project scoped, bounded, and verify size and SHA-256 integrity.
- Delete revokes metadata access before removing bytes. If physical cleanup fails, the asset remains logically deleted and requires garbage-collection retry.
- Metadata is stored in SQLite; POSIX database and directory permissions are private.

## Scanner and API boundary

`mark_scan_result` is an internal trust-boundary method. Only a trusted scanner integration may call it after scanning the exact persisted bytes. A future API must never expose this method directly to end users or accept a boolean scan result from a request body. Scanner outage, timeout, unsupported media, and indeterminate verdicts must leave assets quarantined. A malware scanner is not bundled here.

The current implementation does not provide multipart streaming, browser previews, signed URLs, remote object storage, transcoding, content-disarm/reconstruction, retention scheduling, or a garbage-collection worker. It also does not yet bind asset references to annotation task creation in the API. Do not place confidential client media into production until those pieces and the client data-processing agreement are reviewed.

## Validation

`tests/test_annotation_assets.py` covers quarantine, explicit clean-scan promotion, owner/project isolation, unsupported formats, size bounds, symlink rejection, integrity verification and deletion. Cross-platform CI must pass before merge.

## Authenticated API integration

When both the annotation workbench and asset store are injected into the conversation runtime, the authenticated annotation API exposes:

- `POST /v1/annotation/projects/{project_id}/assets` with `application/octet-stream` body.
- `GET /v1/annotation/projects/{project_id}/assets/{asset_id}` for clean assets only; responses are private, attachment-disposition and `nosniff`.
- `DELETE /v1/annotation/projects/{project_id}/assets/{asset_id}` for owner/project-scoped revocation.

The upload route enforces the configured store byte limit, verifies the project belongs to the authenticated owner, and never accepts a filename or path. A trusted synchronous scanner can be injected as `annotation_asset_scanner(media_type, bytes) -> bool`; `True` promotes to clean, `False` blocks, and missing scanner, exceptions or non-boolean results leave the file quarantined. The scanner callback runs off the event loop. Do not inject a scanner that calls untrusted remote services or exports client bytes without authorization.

This initial API buffers one bounded upload in memory rather than streaming multipart chunks to disk. Deployment-level request timeouts, concurrent upload limits and aggregate per-owner quotas remain required before broad production exposure. The API does not expose the scan promotion method.
