# Immutable workspace snapshot publication race audit

Date: 2026-11-28

## Scope

Reviewed `core/immutable_workspace_staging.py`, focusing on snapshot
publication and cleanup. This was a source-only review. No staging roots,
workspace files, or runtime data were accessed.

## Finding: destination collision check is not atomic with publication

After constructing and verifying a sealed snapshot, the function generates a
random `snapshot_id`, checks it with descriptor-relative `os.stat()`, and
then publishes with descriptor-relative `os.rename()`.

The check and rename are separate operations. If another process sharing the
staging directory creates the destination name after the absence check,
ordinary POSIX rename may replace an existing non-directory entry (and may
replace an empty directory, depending on the exact target type/platform
semantics). This contradicts the docstring's unconditional statement that
existing attempt destinations are never overwritten.

The random 128-bit suffix makes accidental collision extremely unlikely; it
does not provide a no-clobber primitive against a concurrent writer with
access to the staging namespace. This is a publication-integrity / denial or
replacement risk within the staging directory, not a source-workspace escape.
The staging root's same-UID threat limitation remains relevant.

## Remediation

1. Publish using an atomic no-replace primitive supported by the target
   platform (for example Linux `renameat2(RENAME_NOREPLACE)` via a narrowly
   reviewed adapter), or use a different publication protocol whose atomic
   semantics are verified for the supported filesystems.
2. Fail closed when the platform/filesystem cannot provide the required
   no-replace behavior; do not fall back to check-then-rename.
3. Add a deterministic race test that creates the target after the absence
   check and before publication. Assert the existing target remains byte- and
   inode-identical and the new snapshot is not reported as published.
4. Document filesystem/platform support and verify durability behavior around
   publication and parent-directory fsync.

## Separate lifecycle issue

The previously recorded `SecureWorkspaceFiles` root-descriptor close race is
independent. This staging function owns local descriptors for one invocation
and closes them in `finally`; this audit does not claim the same shared-handle
race applies here.

No runtime code changed. Tests were not run locally. No staging data was
created, modified, or removed. No merge or deployment.
