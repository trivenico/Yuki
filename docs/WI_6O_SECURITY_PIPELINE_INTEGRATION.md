# WI-6O — End-to-end security pipeline integration

WI-6O connects the WI-6N durable source cursors to the WI-6M operational health aggregator. Deployments can declare which ingestion sources are required for readiness. Each required source must have a successful cursor commit within the configured age threshold and must not have a recorded source error.

Example wiring:

```python
health = SecurityOperationsHealth(
    monitor, outbox, heartbeats,
    authorize_operator=authorize_operator,
    ingestion_cursors=cursors,
    required_ingestion_sources=("authorization_audit", "workload_key_audit"),
    max_cursor_age=300,
)
```

Use only sources actually enabled in the deployment. A missing required cursor, never-completed source, stale last-success timestamp, or recorded source error marks the `ingestion_cursors` check unhealthy. The health response exposes only per-source freshness, initialization, error-presence, and lease-active booleans; it does not expose cursor error codes, worker IDs, event payloads, or identities. An active lease is informational and does not itself fail readiness.

The ingestion worker should heartbeat after a successful poll/commit cycle. Cursor freshness is the authoritative indication that the configured source has actually been polled and imported; worker liveness alone is insufficient. Alert-sync and alert-dispatch workers continue to heartbeat after successful work cycles. The outbox remains at-least-once, and providers should deduplicate by alert ID.

The resulting readiness snapshot combines monitor aggregates, outbox backlog/dead letters, worker heartbeats, and required source-cursor freshness. These stores remain separate SQLite databases: the snapshot is not cross-database atomic, and a brief inconsistency is possible. Configure a cursor age threshold appropriate to the source polling interval and operational recovery objective.

The endpoint remains private and host-authenticated. For multi-host production, replace local SQLite state with shared transactional storage or per-instance aggregation, and add external metrics, alerting, retention, backups, and supervised worker orchestration. This integration does not itself create or deploy workers, configure SIEM/provider credentials, or guarantee delivery to external systems.

Tests were added but were not executed in this connector-only session.
