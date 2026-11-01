# Yuki Evolution Ledger

`automation.evolution_ledger.EvolutionLedger` is a SQLite-backed, append-only
metadata journal for supervised self-evolution.

## Usage

```python
from automation.evolution_ledger import EvolutionLedger

ledger = EvolutionLedger(".yuki/state/evolution.sqlite3")
event = ledger.append(
    event_type="proposal_created",
    request_id="dependency-upgrade-2026",
    actor_id="authenticated-operator-id",
    details={"component": "planner", "status": "proposed"},
)
assert ledger.verify_chain()
print(ledger.head())
```

## Event types

The initial allowlist covers proposal and plan creation, approval or denial,
execution start/completion/failure, independent review, verification, and
rollback completion. Add event types through reviewed code changes, not
arbitrary caller-supplied strings.

## Data minimization

- Actor identifiers are SHA-256 hashed before persistence.
- Details use a strict key and type allowlist with bounded values.
- Prompts, source code, raw agent output, credentials, personal data, and
  arbitrary exception text must never be written to this ledger.
- The event API returns metadata only.

## Integrity model

Each event hash covers its sequence, event ID, timestamp, event type, request
ID, actor hash, canonical details, and previous event hash. Appends use a
SQLite immediate transaction so concurrent writers receive a single ordered
chain. `verify_chain()` detects modified, deleted, reordered, or malformed
records in the local event sequence.

A local hash chain is tamper-evident, not immutable against an administrator
who can rewrite the database and recompute all hashes. Production deployments
must periodically anchor the head hash outside the host—for example, in an
independently controlled append-only object store—and back up the SQLite
database with the rest of Yuki's recovery state.

## Tests

Run:

```bash
python -m unittest tests.test_evolution_ledger
```

Tests cover metadata allowlisting, actor redaction, persistence, concurrent
append ordering, request filtering, and tamper detection.
