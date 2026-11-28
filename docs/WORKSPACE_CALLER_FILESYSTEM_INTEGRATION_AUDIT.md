# Workspace caller integration and path-race audit

Date: 2026-11-28

## Read-only specialist tools

`automation/authorized_read_tools.py` constructs a canonical workspace path
and validates requested paths with `Path.resolve(strict=True)` followed by
`relative_to(root)`. It excludes selected secret/config path components and
bounds result sizes. Listing skips symlinks; individual reads resolve the
target before calling `is_file/stat/read_text`; recursive search similarly
resolves paths before subsequent path-based reads.

These checks provide useful containment against ordinary traversal and
static symlink escapes. They do not bind the later filesystem operation to
the object checked during resolution. A path component or final entry can be
replaced between validation and open/read (TOCTOU). The code does not use
`WorkspaceResourceScopeResolver` or `SecureWorkspaceFiles`.

## Approved workspace edits

`automation/approved_workspace_changes.py` requires a trusted principal,
explicit allowed paths, content digests, and approval for the exact proposed
change. `_safe_existing_file` rejects observed symlink components and checks
resolved containment. The apply path later reads and replaces files through
ordinary `Path` operations. It rechecks content digests before replacement,
but the check and `os.replace` are still separate path-based operations; a
filesystem namespace change can race those checks. Temporary files are
created in the target parent and atomically replaced, which helps with
partial-write safety but does not provide descriptor-anchored containment.

## Separate legacy helpers

`skills/file_skill.py` resolves requested paths and enforces containment,
then lists using path-based operations. `yuki_tools.py` creates a
source-relative workspace at import time and performs ordinary directory
iteration and append writes. These are distinct paths from the secure
descriptor-based API and should not be assumed to inherit its guarantees.

`skills/canvas_workspace.py` creates local Markdown files under a
workspace-derived canvas directory. Its title sanitization and refusal to
overwrite an existing destination are useful controls, but it still uses
path-based directory creation and writes rather than the descriptor API.

## Risk and scope

The findings are integration gaps, not proof of an exploitable race in a
particular deployment. The secure APIs are not automatically applied to
legacy callers merely because they exist in the repository. Principal
authorization and digest-bound approval do not replace race-resistant
filesystem operations.

## Recommended remediation

1. Introduce a shared workspace handle that owns a validated root directory
   descriptor and exposes bounded read/list/write methods.
2. Migrate specialist reads and approved edits to descriptor-relative
   no-follow traversal, retaining their current principal, allowlist, audit,
   digest, and approval checks.
3. Either migrate legacy file/canvas helpers or keep them disabled from
   privileged agent tool registries until they have an equivalent boundary.
4. Add race-focused tests that swap path components and final entries between
   validation and use; assert no operation escapes the opened root.
5. Define an explicit policy for unsupported platforms rather than silently
   falling back to path-based operations.

No workspace content was read or modified. No runtime code was changed and
no local tests were run.
