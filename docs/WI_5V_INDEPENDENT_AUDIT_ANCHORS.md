# WI-5V — Independent authorization audit checkpoints

## Finding

WI-5U added a local SQLite hash chain. That protects against accidental row edits and partial corruption, but a privileged actor who can rewrite the entire database can recompute an internally consistent chain. Local integrity alone cannot prove that previously recorded decisions were not erased or replaced.

## Implementation

- Add immutable AuthorizationAuditCheckpoint receipts containing the committed event sequence and chain head hash.
- Define an AuthorizationAuditAnchor protocol for a host-supplied independent append-only service.
- Publish the current SQLite chain head to that external service.
- Verify the entire local chain and compare the externally stored checkpoint with the exact local event at the anchored sequence.
- Reject missing, malformed, truncated, or rewritten anchored prefixes.
- Add tests for successful anchoring, later local appends, truncation detection, and malformed anchor responses.

## Required production deployment

The protocol is intentionally an integration boundary, not a fake cloud service. The host must supply an independently controlled anchor implementation, such as a remote audit service or an append-only object store with separate credentials and retention controls.

The anchor implementation must:
1. Authenticate the Yuki host using a workload identity that is not stored in the audit database.
2. Enforce monotonic event sequence numbers and reject rollback or replacement of an existing checkpoint.
3. Retain checkpoint history under separate administrative controls.
4. Expose the latest authenticated checkpoint to startup/readiness verification.
5. Alert when checkpoint publication falls behind the expected schedule.

The host should publish checkpoints on a short operational interval and before/after backup or restore operations. On startup, production should call verify_anchor and refuse security-sensitive operation if a required anchor is missing or does not match. This PR provides the audit-side protocol and verification methods; it does not configure a remote service, scheduler, or startup policy automatically.

## Recovery

A restored database may legitimately contain events after the last checkpoint only if the restore process can reconcile them against separately retained audit records. If the database is older than the latest external checkpoint, verification fails and the operator must restore a database containing the anchored event or use a separately controlled audit recovery procedure. Never silently reset the anchor to an older local head.

## Limitations

An attacker who controls both the Yuki host and the independent anchor credentials/service can still attack the audit history. Separate credentials, administrative boundaries, immutable retention, and alerting are necessary. Tests were added but not executed in this environment.
