# Configuration path and file-permission boundary audit

Date: 2026-11-28

## Observed behavior

`core.portable_paths.config_path()` selects an explicit `YUKI_CONFIG` path
when configured and resolves it with `Path.resolve()`. The default selection
also resolves an existing XDG or source-checkout config path. Resolution
follows symlinks rather than preserving and rejecting them.

`core.config.load_config(path=...)` accepts a caller-supplied path directly.
The first-run private-directory creation and exclusive mode-0600 file creation
only run when `path is None` and the selected config does not exist. Existing
configuration files are read with `read_text()`; their file type, symlink
status, owner, and permissions are not validated in this function. The explicit
path branch likewise does not establish a private parent or file mode.

## Security implications

- A symlinked config path is followed, so the actual YAML may be outside the
  expected user config directory.
- Existing config files and parents are trusted as-is. If they are shared or
  group/world-readable, any secrets or provider settings stored there may be
  exposed to other local users.
- Callers that pass `path` bypass the first-run private-directory and
  exclusive-create protections entirely.
- The mode-0600 first-run creation is useful but does not prove that an
  existing config remains private or that every supported caller uses that
  branch.

This is a configuration confidentiality/integrity boundary, not evidence that
the current config is actually exposed. No live configuration path or file
was inspected.

## Decision

No runtime change was made. A safe remediation needs an explicit policy for
legacy source-checkout configs and caller-supplied paths, a no-follow open
strategy, regular-file and owner/mode validation, and migration messaging for
existing shared files. Avoid silently chmodding or relocating existing user
configuration. Add tests for explicit paths, symlinked files/parents,
non-regular files, and permissive existing modes on each supported platform.

No configuration was read, created, changed, or migrated. No local tests were
run.
