# Configured workspace root versus secure file-access boundary audit

Date: 2026-11-28

## Path selection

`core.config.load_config()` accepts `workspace` from YAML or
`YUKI_WORKSPACE`. Relative paths are resolved under `data_root()`; absolute
paths are accepted and resolved. `core.portable_paths.workspace_path()`
therefore provides path normalization, not an authorization boundary: it
follows symlinks and does not require the workspace to be owner-private,
owned by the current user, or on a particular filesystem.

## Existing stronger primitives

The repository contains stronger, explicitly scoped building blocks:

- `WorkspaceResourceScopeResolver` requires an existing directory and
  constrains model-facing relative resources to its canonical root. Its module
  documentation explicitly says it does not open files or prevent TOCTOU
  races.
- `SecureWorkspaceFiles` anchors an open root directory descriptor, rejects
  symlinks in traversed components, revalidates resource scope, and performs
  bounded regular-file reads and atomic writes using descriptor-relative
  operations.
- `stage_workspace_snapshot()` stages only explicit allowlisted files and
  applies no-follow, ownership, hard-link, device, and content checks.

These are useful security primitives, but their existence does not mean every
legacy workspace consumer uses them. The config loader itself does not bind
the selected workspace to one of these handles or enforce their POSIX
requirements.

## Risk and limitation

A configured workspace can intentionally point outside Yuki's data directory,
and path resolution alone does not make that destination private or safe.
Any consumer that uses ordinary path-based file operations instead of the
scoped descriptor APIs may remain exposed to symlink/TOCTOU and out-of-root
access risks. This audit does not claim that every consumer is vulnerable;
it records that the configuration boundary alone does not guarantee safe
filesystem access.

## Decision

No runtime change was made. Follow-up should inventory every workspace
consumer and classify it as descriptor-scoped, separately constrained, or
legacy path-based. Then require an explicit operator-approved workspace root
and route model-facing reads/writes through `WorkspaceResourceScopeResolver`
plus `SecureWorkspaceFiles`, with a documented non-POSIX fallback policy.
Tests should exercise symlinked workspace roots, path swaps, and consumers
that bypass the secure handle.

No workspace contents were read or modified. No local tests were run.
