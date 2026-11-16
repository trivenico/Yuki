# Explicit saved-memory retention

## Behavior

Owner-scoped conversation memories may now be saved with an optional expiry.
The default remains permanent until the owner deletes the item or clears their
saved memories. Expiring entries are excluded from list and search immediately
after their expiry time. A bounded pruning operation permanently deletes
expired rows, either globally for a trusted maintenance caller or scoped to one
owner.

Expiry is explicit at the save call; Yuki does not silently assign a retention
period to existing memories. Supported TTL values are 60 seconds through ten
years. Boolean values are rejected even though Python treats bool as an int.

## Storage and migration

The SQLite store adds a nullable expires_at column and an expiry index. Existing
memory rows migrate with NULL expiry, preserving their previous permanent
retention behavior. The additive migration tolerates another process winning
the same column-add race. Capacity checks first remove expired entries for that
owner before considering the existing bounded-entry policy.

## Safety boundaries

- All list, search, delete, clear, and owner-scoped pruning operations retain
  owner isolation.
- Expiry hides an item from retrieval before physical pruning; pruning is the
  permanent deletion step.
- No automatic summarization, implicit memory capture, or cross-owner sharing
  is introduced.
- The memory content remains untrusted reference data when included in a
  conversation prompt.
- This does not encrypt the database; the existing private SQLite path
  protections remain filesystem hardening, not encryption.

## Validation

Tests cover explicit expiry, list/search filtering, owner-scoped pruning,
TTL input bounds, service forwarding, and migration of an existing database
without losing previously saved rows.
