# Phase 107 — Privacy-controlled conflict journal bridge

## Scope

Adds `core/conflict_sync_bridge.py`, a local adapter that serializes a persisted
memory conflict into a versioned `memory.conflict.recorded` event for the existing
SQLite sync journal. It does not open network connections or activate automatic
replication.

## Privacy defaults

- Publishing is rejected unless the caller passes `allow_conflict_sync=True`.
- Event payloads contain conflict IDs, memory IDs, candidate version IDs, origins,
  sequences, and status by default; memory text is excluded.
- Sharing candidate text requires the separate explicit
  `share_memory_text=True` opt-in.
- Text sharing validates candidate text length and category. Payload size remains
  bounded by the underlying sync journal.
- Schema version is explicit (`schema_version: 1`) so later readers can reject or
  migrate unsupported event formats rather than silently misinterpret them.

## Reliability and safety boundaries

- Journal append is local and durable according to the journal's SQLite settings.
- Existing journal event IDs and origin sequences provide the journal's duplicate
  and collision protections during ingest; this bridge does not bypass them.
- No automatic winner selection or overwrite of memory records is performed.
- Authenticated device pairing, encrypted transport, delivery acknowledgements,
  runtime startup wiring, and backup/restore are not included.
- Callers must still enforce user/device authorization and authenticated transport
  before exporting events to another device.

## Verification

Run:

```bash
python -m unittest tests.test_conflict_sync_bridge -v
python -m unittest discover -s tests -v
```

The targeted tests cover opt-in enforcement, metadata-only defaults, explicit
text sharing, versioned event append, and invalid/oversized candidate rejection.
