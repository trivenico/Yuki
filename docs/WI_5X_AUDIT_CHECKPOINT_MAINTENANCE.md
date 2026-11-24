# WI-5X — Bounded audit checkpoint maintenance

## Gap

WI-5W required an independent anchor and exposed repeated integrity verification, but deployments still had to assemble the checkpoint maintenance operation themselves. There was no single bounded operation that verified the existing trust state, published a newer local head, and verified the result.

## Implementation

- Add AuthorizationAuditMaintenance.run_once(), an explicit finite maintenance cycle with no background thread, timer, or hidden network activity.
- Verify the local hash chain before any anchor interaction.
- Require an already provisioned external checkpoint; initial anchor provisioning remains a separate operator-controlled ceremony.
- Verify the current checkpoint before publishing, preventing a corrupted local chain from being blessed by publishing a new head.
- Publish only when the local head differs from the current anchor.
- Verify again after publication and fail closed on anchor outage, mismatch, or publish failure.
- Expose ProductionSecurityContext.audit_maintenance() bound to that context's exact audit writer and anchor.
- Add regression tests for initial provisioning, normal publication, idempotent runs, local tampering, anchor outage, publish failure, and mismatch.

## Scheduler contract

A deployment-owned scheduler should call context.audit_maintenance().run_once() on a fixed interval, record success/failure and checkpoint event ID, and alert on failures or missed intervals. It must not swallow exceptions and report success. Use a single-flight scheduler or allow the anchor implementation to safely serialize concurrent idempotent publication.

Example host integration:

```python
# Called by the deployment's external scheduler, not by an in-process timer.
report = production_security.audit_maintenance().run_once()
metrics.audit_checkpoint_event_id.set(report.checkpoint.event_id)
metrics.audit_checkpoint_published.set(int(report.published))
```

The scheduler must independently call production_security.verify_audit_integrity() as part of readiness and after restore. Checkpoint age/lag thresholds must be set by deployment policy based on the maximum acceptable unanchored audit window.

## Explicit non-goals

This PR does not provision an anchor, implement a remote anchor backend, create a scheduler service, export metrics, or automatically wire a web readiness route. The stock web launcher remains fail-closed unless deployment-owned bootstrap supplies the anchor. Tests are added but were not executed in this environment.
