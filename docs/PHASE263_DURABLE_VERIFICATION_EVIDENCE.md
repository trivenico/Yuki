# Phase 263 — Durable verification evidence

## Delivered

Added `automation/verification_evidence_store.py`, a SQLite-backed append-only
store for independent verification reports.

- Validates reports through the Phase 262 fail-closed evaluator.
- Derives deterministic report IDs from canonical serialized report contents.
- Makes exact retries idempotent.
- Rejects report identity collisions with different serialized contents.
- Persists both the report and its evaluated decision.
- Supports bounded report lookup and per-artifact history.
- Persists rejected reports as evidence of failed, blocked, or unrun checks.

Added `tests/test_verification_evidence_store.py` for persistence across store
reopen, idempotent retries, rejected reports, and bounded artifact history.

## Security and integration limitations

The store is append-only through this API, but SQLite file permissions and
database access must be protected operationally. It does not authenticate
reviewer identities, execute test suites, run security scanners, or verify that
evidence references are genuine. Those responsibilities belong to trusted
verification workers and their execution adapters.

This phase deliberately does not change coordinator completion semantics.
The coordinator does not yet require a stored accepted report before marking
subtasks accepted. That integration must follow only after trusted evidence
producers and artifact storage are connected.

Tests are authored but not executed here. No CI result is claimed.
