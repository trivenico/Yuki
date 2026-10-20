# Phase 45 — OS sandbox backend prototype

## Audit outcome

The repository had no OS-level sandbox implementation. `SubprocessExecutor` provides bounded process execution but runs with the current user's privileges. Production dispatch remains disabled, and this phase does not re-enable it.

## Increment delivered

Added `core/os_sandbox.py` with a Linux Bubblewrap adapter that:
- refuses non-Linux hosts;
- requires an explicit, non-root minimal root filesystem containing a regular `.yuki-sandbox-rootfs` marker;
- rejects group/world-writable root filesystems and missing Bubblewrap;
- uses Bubblewrap's isolated namespaces (`--unshare-all`), read-only rootfs, private proc/dev, temporary tmpfs, and per-invocation writable scratch at `/work`;
- preserves bounded JSON I/O and time limits through the existing subprocess runner.

Added `tests/test_phase45_os_sandbox.py` for platform refusal and fail-closed rootfs/binary validation.

## Important limitations

This is a **backend prototype**, not production enablement:
- It has not been exercised in a real Bubblewrap-capable CI host in this change.
- A minimal rootfs must be built and audited for each skill runtime; the host root must never be used.
- The current dispatcher still rejects all production subprocess skill registration. The new backend is not yet wired into a trusted production adapter.
- Windows and macOS backends are not implemented and remain unsupported.
- Bubblewrap and kernel vulnerabilities, host configuration, resource limits, syscall policy, and sandbox escape resistance need independent security review.
- Existing subprocess timeout kills the direct child; process-tree cleanup and CPU/memory/pid quotas need further hardening before untrusted workloads.

Do not set a sandbox-enabled flag or re-enable production execution based on this prototype alone. Next steps: add process-tree/resource controls, establish reproducible minimal rootfs build tooling, integrate through a reviewed adapter, add real Linux integration CI, and implement independently reviewed Windows/macOS backends.
