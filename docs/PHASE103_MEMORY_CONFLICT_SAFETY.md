# Phase 103 — Memory compatibility and conflict safety

## Implemented scope
- Add a pure, local conflict classifier for versioned memory records.
- Detect identical versions and explicitly linked parent/child versions.
- Treat concurrent or ancestry-unknown edits as conflicts.
- Preserve both versions and produce a deterministic conflict identifier.
- Validate IDs, sequence numbers, category, and text bounds.
- Add focused regression tests.

## Safety boundary
This is a conflict-detection primitive, not a complete memory migration or sync integration. It does not write to the memory store, choose a winner, make network requests, or export personal information. Callers must preserve returned versions and require an explicit user decision before resolving a conflict.

## Remaining work
- Add a reviewed adapter for legacy `yuki_memory.json` and learning history.
- Define persistence for version ancestry and conflict resolution audit events.
- Integrate with the journal only after the data schema and privacy allowlist are approved.
- Add authenticated transport, device revocation, offline retries, encrypted backup, and restore tests.
- Run CI on the PR head and review before merge.
