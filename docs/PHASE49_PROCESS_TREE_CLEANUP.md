# Phase 49 — POSIX subprocess process-tree cleanup

## Audit finding

The bounded subprocess runner previously called `Popen.kill()` on timeout and output overflow. That kills only the direct child on POSIX; a spawned descendant could survive after the approved invocation ended.

## Change

- POSIX child processes now start in a new session/process group.
- Timeout and stdout/stderr overflow terminate the entire process group with `SIGKILL`, then reap the direct child.
- Added regression tests that spawn a sleeping descendant and exercise timeout and output-overflow cleanup.

## Platform limits

- On Windows, this change still terminates only the direct child. A Windows Job Object implementation is required before claiming process-tree cleanup there.
- This does not enforce CPU, memory, PID, syscall, filesystem, or network quotas.
- The generic subprocess executor remains process lifecycle control, not an OS sandbox. Production skill dispatch remains disabled until the sandbox boundary is independently reviewed.
- Real Bubblewrap integration tests on a capable Linux runner remain a separate required gate.
