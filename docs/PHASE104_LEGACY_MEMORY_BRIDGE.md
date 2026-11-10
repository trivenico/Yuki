# Phase 104 — Legacy memory compatibility bridge

## Scope
- Read legacy `yuki_memory.json`-style notes and `yuki_learning.json`-style outcomes.
- Produce a MemoryStore-compatible unified JSON snapshot, preserving source IDs, timestamps, categories, and learning fields.
- Redact common API-key/token patterns in imported content.
- Dry-run by default; apply requires an explicit `apply=True`.
- Create exclusive backups before writing and never modify source files.
- Refuse malformed sources, existing destination files, and backup collisions.

## Use
```python
from core.legacy_memory_bridge import bridge_legacy_memory

# Preview only; does not write anything.
preview = bridge_legacy_memory(
    "yuki_memory.json",
    "yuki_learning.json",
    "yuki_unified_memory.json",
)

# Apply only after reviewing preview counts and destination.
result = bridge_legacy_memory(
    "yuki_memory.json",
    "yuki_learning.json",
    "yuki_unified_memory.json",
    apply=True,
)
```

## Safety limits
This utility is not wired into startup and does not modify existing user files unless explicitly invoked with apply=True (and even then only writes a new destination and backups). It does not upload or sync memory. Review imported content before using it as a live repository; redaction patterns are defense-in-depth, not a guarantee that every secret is detected.

## Follow-up
- Run CI on this PR and review the importer.
- Add stable version ancestry and persistent conflict resolution events.
- Integrate the repository only after callers are migrated and the user approves the target paths.
- Device pairing, network authorization, offline sync, encrypted backup/restore, and deployment remain separate phases.
