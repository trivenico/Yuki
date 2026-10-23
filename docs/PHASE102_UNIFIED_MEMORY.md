# Phase 102 — Unified local memory and migration

## Scope
- Provide a single local-only `MemoryRepository` interface for structured notes and lexical search.
- Provide an explicit migration helper for legacy string-list or structured-note JSON.
- Keep migration dry-run by default; preserve source and create a backup before apply.
- Add focused regression tests.

## Safety properties
- No network access, sync transport, automatic startup migration, or remote export.
- Destination must be a new file; source and destination cannot be the same.
- Invalid, unreadable, unsupported, or oversized input fails closed.
- Existing destinations and existing migration backups are never overwritten.
- Source remains untouched; migration is opt-in through `apply=True`.

## Current limitations
This phase does not replace every legacy memory call site, merge task-learning history, migrate conversation logs, enable cloud sync, or provision Oracle Cloud. The unified repository defaults to a distinct `yuki_unified_memory.json` file to avoid silently interpreting legacy data with the wrong schema. Migration should be reviewed and explicitly run only after identifying the intended source and destination on the user's machine.
