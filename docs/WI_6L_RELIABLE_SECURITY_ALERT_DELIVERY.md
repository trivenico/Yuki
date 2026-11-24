# WI-6L — Reliable security alert delivery

SecurityAlertOutbox stores normalized incident snapshots in a durable SQLite
outbox. The payload is allowlisted to incident ID, category, severity, status,
timestamps, event count, and reason code; it cannot carry credentials, request
bodies, prompts, or arbitrary metadata.

The dispatcher contract is provider-neutral. `sync_incidents()` reads open and
acknowledged incidents from the WI-6K monitor and idempotently snapshots them
into this outbox; a supervised worker can run it periodically before draining
due alerts. The dispatcher contract is:
- enqueue an incident snapshot; repeated enqueue of the same incident version
  returns the same alert ID;
- claim due work under a bounded lease with a unique fencing token, preventing
  an expired or superseded worker from acknowledging a newer claim;
- call a deployment-owned sender with (payload, alert_id);
- acknowledge delivery or schedule exponential retry;
- move exhausted attempts or repeatedly expired worker leases to dead-letter
  state for operator review.

Delivery is at-least-once. A process may crash after a provider accepts an
alert but before local acknowledgement, so providers should deduplicate on
alert_id. Expired leases are reclaimable. Sender exception text is never
persisted; only stable error codes are retained. Provider credentials and
network clients remain outside Yuki's core.

The outbox is a separate SQLite file in this initial implementation. It does
not yet provide atomic commit with the incident database; a deployment worker
must poll incidents and enqueue them idempotently. Production multi-host use
requires a shared transactional outbox, monitoring of pending/dead-letter
backlogs, alert delivery SLOs, and external archival. The host must securely
configure and supervise the sender worker.
Tests were added but not executed locally in this connector-only session.
