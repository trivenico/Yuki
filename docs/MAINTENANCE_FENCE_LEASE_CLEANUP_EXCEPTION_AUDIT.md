# Maintenance Fence Lease Cleanup Exception Audit

## Scope

Reviewed `core/maintenance_fence.py` at PR #1346 head `d67fd05b90ddd6bee467b73088b213b461f37c46`, focusing on exception safety while releasing a lease.

## Finding: OS unlock/close errors can strand the in-process lock

**Severity: Low (availability / recovery)**

The lease's outer `finally` releases the per-path `thread_lock` only after OS descriptor cleanup completes. When `os_locked` is true, `_unlock_os(fd)` runs in a `try` whose `finally` calls `os.close(fd)`; if either operation raises, control skips `_active_fds.discard(fd)` and, critically, `thread_lock.release()`. In the not-locked branch, an `os.close(fd)` error similarly skips both later cleanup steps.

The context manager can therefore propagate a cleanup exception while leaving the per-path reentrant lock acquired. Other threads using the same path may then block until their acquisition timeout; nested or repeated use in the affected thread can also behave unexpectedly. The OS lock may be released by descriptor close, but the in-process coordinator is not guaranteed to recover.

This requires an OS-level unlock/close failure (or abnormal descriptor state), so it is an availability and cleanup robustness issue rather than a normal-path concurrency flaw.

## Recommendation

Use nested `finally` blocks so descriptor tracking and the in-process lock release are unconditional, regardless of unlock/close failures. Preserve the first cleanup exception while still attempting all cleanup actions. Add fault-injection tests for unlock failure and close failure, asserting a subsequent thread can acquire the same path and descriptor bookkeeping is cleaned.

## Validation and limitations

Source review only; tests were not run and no OS-level failure was reproduced.