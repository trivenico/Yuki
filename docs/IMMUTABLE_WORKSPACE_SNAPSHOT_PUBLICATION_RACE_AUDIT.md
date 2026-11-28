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

## Finding: directory fsync failure creates an ambiguous published outcome

The implementation sets `published = True` immediately after
`os.rename()`, then calls `os.fsync(destination_fd)`. If that fsync raises,
the outer handler converts the `OSError` to
`WorkspaceStagingError("workspace_snapshot_creation_failed")`. Because
`published` is already true, cleanup deliberately does not remove the
renamed directory. The caller receives a failure without the `snapshot_id`,
although the snapshot directory may already be visible in the staging root.

This is a durability/transaction-outcome ambiguity, not evidence that the
rename was rolled back. A blind retry can create a second snapshot and leave
the first orphaned. The implementation currently has no typed "published but
durability uncertain" result or reconciliation token.

### Remediation

1. Preserve the generated snapshot ID in a typed exception/result that
   explicitly distinguishes pre-publication failure from post-rename
   durability uncertainty.
2. Define a caller reconciliation procedure that inspects the exact
   generated destination and validates its manifest before retrying; never
   blindly retry an ambiguous publication.
3. Add fault-injection coverage for directory-fsync failure after successful
   rename. Assert the destination remains, the caller can identify it, and
   retry/reconciliation does not duplicate publication.
4. Keep the no-replace publication fix above: outcome reporting does not
   repair the separate check-then-rename race.

## Finding: unpublished snapshot cleanup failures are silent

The outer `finally` block attempts to remove the temporary staging tree when
publication has not occurred. It catches and discards every `OSError` from
`_remove_tree_at()`. The original staging error remains visible, but callers
cannot distinguish successful cleanup from a partially removed temporary
tree. A cleanup failure can therefore leave staged workspace contents and
consume profile disk space until an operator discovers the orphan.

The staging root is validated as a private profile directory and the temporary
tree is created with restrictive permissions, so this is not evidence of
cross-user disclosure. It is a data-retention, disk-exhaustion, and recovery
observability gap. The risk increases if the staging root is writable by
multiple cooperating same-UID processes or repeated failures occur.

### Remediation

1. Preserve the primary staging exception while attaching a structured
   cleanup-incomplete indicator and the generated temporary directory name.
2. Emit a bounded, secret-free cleanup event/diagnostic that identifies the
   orphan without logging file contents or workspace paths unnecessarily.
3. Provide a separately authorized orphan-reconciliation routine that opens
   entries descriptor-relatively, refuses symlinks and unexpected ownership,
   and applies an explicit age/attempt policy before removal.
4. Add fault-injection tests where unlink/rmdir fails partway through cleanup;
   assert the primary error is preserved and cleanup incompleteness is
   observable without claiming the tree was removed.

This is lower severity than the no-replace publication race and the
post-rename durability ambiguity above. No runtime code changed.

## Finding: per-file stability does not establish a coherent tree snapshot

The copy loop opens and copies each allowlisted file in sequence. It compares
that file's device, inode, size, mtime and ctime before and after its own copy,
which detects many mutations during that individual read. It does not freeze
or identify the complete source tree before copying, nor does it revalidate a
canonical tree/manifest identity after all files have been copied.

A workspace can therefore change between copies: file A may be copied from
revision N, then a concurrent edit changes files A and B, and file B may be
copied from revision N+1. Both individual copies can pass their per-file
stability checks while the resulting manifest represents no coherent
point-in-time workspace state. The staging contract explicitly requires a
clean reviewed Git tree or an explicitly enumerated reviewed input manifest;
the current prototype does not establish that binding itself.

This is a snapshot-integrity and reproducibility gap. It does not by itself
demonstrate path escape or secret disclosure, and the current prototype is
not wired into sandbox execution.

### Remediation

1. Bind staging to an immutable source identity: preferably a reviewed Git
   tree/commit plus exact approved patch digest, or a caller-supplied canonical
   manifest whose entries bind path, inode/content digest and policy revision.
2. Reject dirty/untracked inputs unless they are explicitly represented in
   the reviewed manifest.
3. Revalidate the complete source identity after copying and abort if it
   changed; for stronger guarantees, stage from an immutable checkout or
   filesystem snapshot rather than a mutable live workspace.
4. Add deterministic multi-file race tests that mutate a later file after an
   earlier file has been copied. Assert staging cannot return a successful
   snapshot whose files come from mixed source generations.

The prototype's per-file before/after metadata check remains useful but is not
a substitute for whole-tree snapshot consistency.
