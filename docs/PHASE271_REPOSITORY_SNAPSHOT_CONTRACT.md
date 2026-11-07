# Phase 271 — Deterministic repository snapshot contract

## Delivered

Added `automation/repository_snapshot.py` with a bounded in-memory repository
snapshot contract:

- Validates normalized relative POSIX paths and rejects traversal, absolute
  paths, NUL bytes and backslash path ambiguity.
- Enforces configurable file-count, per-file and aggregate byte limits.
- Excludes common generated/vendor directories by default.
- Computes per-file SHA-256 digests and a deterministic aggregate snapshot
  digest independent of mapping insertion order.
- Materializes exact file bytes into a new or empty destination, checks an
  optional expected digest, refuses overwrites and rejects symlink path
  components encountered during materialization.

Tests are in `tests/test_repository_snapshot.py`.

## Boundaries and remaining integration

This is a deterministic snapshot format and materializer, not a Git object
reader, dependency lockfile resolver, hermetic build system, or OS sandbox.
The caller must supply a trusted mapping of repository-relative paths to bytes.
The materializer's symlink checks are defense in depth; they are not a
replacement for OS-level sandboxing or race-free directory-handle APIs.

The next integration step is to source the mapping from a pinned Git commit,
persist the manifest alongside the engineering artifact, and execute
verification commands inside a separately provisioned restricted workspace.
Dependency installation and network access must remain disabled unless an
operator-approved, hash-locked provisioning step is used.

## Verification status

The new tests have been authored but not executed in this environment.
No CI result is claimed for this commit.
