# Secure workspace handle lifecycle race audit

Date: 2026-11-28

## Scope

Reviewed `core/secure_workspace_files.py` and
`tests/test_secure_workspace_files.py`. This is a source-only review; no
workspace data was accessed and no tests were executed.

## Finding: close can race an active operation

`SecureWorkspaceFiles` stores one shared root descriptor in `_root_fd`.
Operations call `_assert_root_identity()`, which checks that descriptor, and
later `_open_parent()` duplicates the shared descriptor with `os.dup()`.
The sequence is not protected by a lock or an operation lease. Meanwhile,
`close()` can close that descriptor and set the field to `None`.

A concurrent close can therefore occur between identity validation and
descriptor duplication. Depending on scheduling, the operation may fail with
an OS error, or the descriptor number may have been reused before `os.dup`
runs. In the latter case, the operation could duplicate an unrelated open
descriptor and perform its relative traversal from the wrong directory.
The code's later resource-scope checks do not prove that this duplicated FD
is the originally validated root. This is a potential cross-directory
confinement failure when one handle is shared across threads and closed
concurrently, not evidence that a current host actually does so.

## Existing lifecycle coverage

The current tests verify ordinary close followed by read rejection, root
replacement detection, and symlink races during resolver-to-open. They do
not exercise concurrent `close()` versus `read_bytes()` / `write_bytes()`,
or multiple simultaneous operations and shutdown.

## Required correction

1. Protect root-descriptor state with a lock.
2. At operation entry, atomically verify the handle is open and duplicate the
   root descriptor while holding that lock. The operation must use only its
   private duplicate for all subsequent traversal.
3. Make `close()` acquire the same lock, mark the handle closed, and close
   the owned descriptor exactly once. Existing operation duplicates remain
   valid until each operation closes its own descriptor.
4. Ensure all error paths close the per-operation root duplicate.
5. Add deterministic concurrency tests using synchronization barriers:
   close between validation and traversal, close during read/write, repeated
   close, and simultaneous independent operations. Assert operations either
   complete beneath the originally opened root or fail closed—never use a
   descriptor acquired from another resource.

This lifecycle fix is separate from the previously documented conditional
publication requirement (expected digest / expected absence), which addresses
lost updates rather than root-descriptor ownership.

No runtime code changed. Tests were not run locally. No merge or deployment.
