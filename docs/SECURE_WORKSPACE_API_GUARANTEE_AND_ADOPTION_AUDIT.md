# Secure workspace API guarantee and adoption audit

Date: 2026-11-28

## Scope

Read-only source review of `core/workspace_resource_scope.py`,
`core/secure_workspace_files.py`, and the workspace consumers identified in
`docs/WORKSPACE_CALLER_FILESYSTEM_INTEGRATION_AUDIT.md`. No workspace files,
configuration, or runtime data were accessed.

## What the secure API actually guarantees

`WorkspaceResourceScopeResolver` rejects model-facing absolute paths,
traversal components, control characters, alternate-stream syntax, and
resolved targets outside its configured root. It explicitly performs path
validation only; it does not authorize an actor or close filesystem races.

On POSIX, `SecureWorkspaceFiles` opens the workspace root and performs
component traversal relative to directory descriptors with
`O_DIRECTORY | O_NOFOLLOW`. Reads open the final entry with `O_NOFOLLOW`,
require a regular file, and enforce byte limits. Writes create a same-directory
exclusive temporary file, set a restrictive mode, fsync it, and rename it
relative to the pinned parent directory descriptor. This prevents ordinary
symlink-following and parent-path substitution during the final operation.

The class is intentionally unavailable where the required POSIX primitives
are absent. It does not validate root ownership or private directory mode;
those remain responsibilities of the composition/authorization boundary.

## Remaining write semantics

`write_bytes` revalidates the resource scope and checks that an observed
existing destination is a regular file. It then publishes with descriptor-
relative `rename`. The destination is not conditionally bound to the inode
or digest observed during that check:

- If the leaf is replaced after the regular-file check, the rename replaces
  the entry currently at that name (a final symlink is replaced, not followed).
- If the leaf was absent but another writer creates it before publication,
  the rename can replace that new entry.
- Concurrent writers can therefore overwrite one another's updates even
  though the operation remains anchored beneath the opened workspace root.

This is a lost-update / stale-authorization concern, not evidence of a
workspace escape. The API currently offers no expected-digest or
expected-absent publication contract.

## Adoption gap

The previously traced specialist read tools, approved workspace changes,
legacy file skill, legacy `yuki_tools.py`, and canvas helper do not call
`SecureWorkspaceFiles`. Their path-based validation and authorization
controls do not inherit the descriptor API's race resistance. In particular,
the approved multi-file edit flow has digest checks but its check and
`os.replace` are separate path-based operations.

No evidence in this audit establishes that the secure API is wired into every
agent-facing or legacy workspace path. Standalone constructors and external
hosts remain separate reachability questions.

## Remediation sequence

1. Add explicit write preconditions: expected digest for replacement and
   expected-absent for creation; fail closed when the precondition no longer
   holds. Define multi-file transaction/rollback semantics separately.
2. Keep root privacy and principal authorization in the composition layer;
   validate that the opened root is the intended owner-private workspace.
3. Migrate privileged read and edit consumers to a shared descriptor-backed
   workspace handle; do not silently fall back to path-based operations.
4. Gate legacy file/canvas tools out of privileged registries until migrated
   or explicitly classified as untrusted/manual-only.
5. Add adversarial tests for final-entry swaps, concurrent create/replace,
   parent-component swaps, root replacement, and unsupported platforms.

No runtime code changed. No local tests were run. No migration, workspace
mutation, merge, or deployment occurred.
