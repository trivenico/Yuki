# Phase 162.2 — Cross-platform hardware snapshot

## Implemented

- Read-only operating-system and CPU architecture identification.
- Logical CPU count with invalid/unavailable values represented as unknown.
- Total and available physical memory using POSIX sysconf or Windows GlobalMemoryStatusEx.
- Explicit GPU unknown/not-probed status; no GPU capacity is guessed.
- No subprocesses, network calls, telemetry upload, model downloads, or activation.

## Limitations

- Memory values are point-in-time snapshots and can change immediately.
- POSIX available-page semantics vary by platform; values are advisory.
- GPU inventory requires a separately reviewed platform-specific adapter and must not be inferred from CPU or OS metadata.
- Hardware snapshot values are local diagnostics, not trusted remote authorization inputs.

## Validation

Unit tests cover platform metadata, unknown values, GPU non-detection, and POSIX available-memory clamping. Windows API behavior remains covered by Windows CI runtime and should be supplemented with a dedicated mocked Win32 test before release.
