# WI-6M — Security operations health and observability

Adds an access-controlled health aggregator for the WI-6K monitor and WI-6L outbox, plus durable worker heartbeat timestamps.

The health snapshot contains only bounded aggregate counts and ages: monitor event count/open incident count/last event age; outbox pending, inflight, delivered and dead-letter counts, oldest pending age, max attempts and last delivery age; and freshness for ingestion, alert-sync and alert-dispatch workers. It never returns incident records, alert payloads, subject hashes, credentials, or exception text.

Readiness is unhealthy when the ingestion/alert-sync/alert-dispatch worker heartbeat is absent or stale, dead letters exist, or pending+inflight backlog exceeds the configured threshold. Event age is informational; a quiet system with no new security events is not itself an ingestion failure. This is an operational signal, not proof that upstream sources are complete: deployments must heartbeat ingestion only after successful source polling and cursor persistence, and alert-sync only after a successful sync. The module intentionally does not invent or persist source cursors; the worker owns cursor durability and must advance them only after successful ingestion.

SecurityWorkerHeartbeat stores one timestamp per role in a private SQLite database. Roles are ingestion, alert_sync and alert_dispatch. Health aggregation reads the monitor and outbox databases independently; snapshots are not cross-database atomic and may be briefly inconsistent.

Mount GET /health/security-operations only on a private, independently protected health listener. The host must authenticate each request through authenticate_operator; operator authorization is checked again by the health service. Generic 403/503 responses avoid leaking internal failures. Do not expose this endpoint publicly.

SQLite remains a single-host starter. Multi-host deployments need shared transactional state or per-instance aggregation, external metrics/alerting, retention and backup policies, supervised workers, and tested recovery. Tests were added but not executed in this connector-only session.
