# Phase 47 — First versioned built-in skill contract

## Scope

Migrated only the read-only local clock skill to the manifest-dispatched contract. The adapter defines `system.time.read@1.0.0`, requires the exact `system.clock.read` capability grant, accepts an empty JSON object, and returns an object containing `local_time`.

## Runtime shape

- `core.builtin_skill_adapters.time_skill_manifest()` returns validated manifest metadata.
- `time_skill_command()` returns a fixed `sys.executable -m core.builtin_skill_worker` command; no user-supplied module/command is interpolated.
- `core.builtin_skill_worker` has a strict allowlist and bounded JSON stdin/stdout. It cannot choose arbitrary skill classes from the request.
- Integration tests use the actual subprocess executor and a one-time approval store in development/test mode.

## Security limits

This is a first contract migration, not production enablement. The current `SubprocessExecutor` is not an OS sandbox; production dispatch remains disabled. The adapter is a migration seam and is not automatically registered in the default application composition. Do not expose it to untrusted callers until the runtime uses a reviewed OS isolation backend.

## Follow-up

Migrate further skills one at a time only after schema tests, denial/replay tests, and capability review. Avoid migrating file or system skills until filesystem scope, path policy, and least-privilege boundaries are explicit.
