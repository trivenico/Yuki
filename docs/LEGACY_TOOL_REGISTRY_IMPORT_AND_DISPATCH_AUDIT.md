# Legacy tool registry import and guarded-dispatch audit

Date: 2026-11-28

## Import-time behavior

`yuki.py` imports `TOOLS` and `run_tool` from `yuki_tools.py` at module
scope. Importing `yuki_tools.py` computes a source-relative
`yuki_workspace` directory and immediately calls
`WORKSPACE.mkdir(parents=True, exist_ok=True)`. This filesystem side effect
therefore occurs before the legacy terminal enters its command loop and even
when no workspace tool is invoked. It is not routed through the portable data
root, private-profile validator, MaintenanceFence, or secure workspace file
API.

The registry includes `list_files` and `save_note`; the latter appends to
`notes.txt` using ordinary path-based file access.

## Guarded dispatch behavior

The legacy `/tool` command constructs `GuardedToolRuntime(TOOLS, policy)`
without owner ID, session ID, or a live identity validator. The current
`GuardedToolRuntime` constructor rejects every non-empty registry unless all
three are supplied. Consequently, this legacy command path fails closed before
a handler is invoked; the outer terminal loop reports a recoverable error.
This is an availability/compatibility defect, not evidence that the tool
handlers are currently reachable through this command.

The older `run_tool()` function directly dispatches from the module registry,
but the inspected `yuki.py` command handler uses `GuardedToolRuntime`, not
`run_tool()`. This audit does not establish whether external consumers import
and call `run_tool()`.

## Security and maintenance implications

- Importing the legacy terminal can create a writable directory in the
  repository/source tree.
- If a caller invokes `run_tool()` directly, it bypasses the newer
  owner/session-bound guarded runtime and can call the append writer.
- The guarded `/tool` path currently rejects construction, so it does not
  provide a working authenticated path to these handlers.
- The registry's file operations are not descriptor-anchored and do not
  inherit the secure workspace guarantees.

## Recommended remediation

1. Remove filesystem mutation from module import; create workspace storage
   only through an explicit, validated initialization path.
2. Move the legacy registry to the same private-profile and descriptor-based
   workspace boundary as supported tools, or retire the duplicate registry.
3. Remove or explicitly deprecate direct `run_tool()` dispatch; do not repair
   `/tool` by weakening GuardedToolRuntime's owner/session requirements.
4. Add tests for import purity, authenticated dispatch construction, and
   rejection of direct legacy dispatch from unsupported entry points.

No workspace directory was created, inspected, or modified during this audit.
No runtime code changed and no local tests were run.
