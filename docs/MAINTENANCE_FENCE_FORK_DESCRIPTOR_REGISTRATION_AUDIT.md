# Maintenance Fence Fork Descriptor Registration Race Audit

## Scope

Reviewed `core/maintenance_fence.py` and `tests/test_maintenance_fence.py` at PR #1346 head `9a08d8937658702d1d0fcac7b6d8a820085bcee7`, focusing on POSIX fork cleanup and lock descriptor lifetime.

## Finding: fork can inherit a lock descriptor before it is registered for child cleanup

**Severity: Low (availability / maintenance coordination)**

The fence tracks open lock descriptors in the process-global `_active_fds` set. `_open_lock_file()` calls `os.open()`, performs descriptor/path validation, and only then adds the descriptor to `_active_fds`. The `os.register_at_fork(after_in_child=...)` handler closes only descriptors present in that set.

If a multithreaded process forks after `os.open()` succeeds but before `_active_fds.add(fd)`, the child inherits the descriptor but its at-fork handler cannot see or close it. The parent can subsequently acquire the POSIX `flock` and exit its lease, while the child retains the inherited descriptor. Since `flock` ownership is associated with the open file description and remains until its references are closed, the leaked child descriptor can keep the maintenance lock held after the parent believes it released it. This can cause later cooperating operations to time out until the child exits or closes the descriptor.

This is a narrow timing window requiring a fork concurrent with lock-file setup; it is not an authorization bypass or data-integrity issue. Existing tests cover fork while the parent already holds a registered lease, not fork during descriptor registration.

## Recommendation

Coordinate fork preparation with descriptor registration and lock lifecycle, or use a design that cannot leave an untracked inheritable descriptor across fork. Keep descriptor creation non-inheritable where supported, and add a deterministic regression test that forks at the open-before-registration boundary. Ensure child cleanup is robust even when fork overlaps lock setup.

## Validation and limitations

Source review only; tests were not run. No production reproduction was performed. This finding is limited to POSIX fork-based processes and the descriptor-registration window.