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
