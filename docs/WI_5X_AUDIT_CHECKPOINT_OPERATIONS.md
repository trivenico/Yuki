# WI-5X — Audit checkpoint freshness and operations

## Problem

WI-5V added an independent checkpoint contract and WI-5W required a valid checkpoint during production security context construction. However, a checkpoint that was once valid could remain valid indefinitely if its anchored prefix still existed locally. That did not prove the publisher was keeping pace with the audit stream.

## Changes

- Extend AuthorizationAuditCheckpoint with a UTC publication timestamp.
- Add optional freshness enforcement to SQLiteAuthorizationAuditLog.verify_anchor(). ProductionSecurityContext defaults to a 300-second maximum checkpoint age and rechecks freshness on every verify_audit_integrity() call.
- Reject malformed timestamps, timezone-naive timestamps, timestamps more than 30 seconds in the future, stale checkpoints, and invalid freshness budgets.
- Add AuthorizationAuditCheckpointPublisher.run_once() for an externally managed scheduler. It verifies the existing anchor before publishing, refreshes a checkpoint when the event head is unchanged, advances it when new events exist, verifies the result, and refuses to silently initialize a missing anchor.
- Document operator-controlled first-checkpoint provisioning, scheduler cadence, telemetry, and failure alerting.

## Security boundaries and limitations

- The publisher is intentionally a one-shot operation. It does not spawn a thread, timer, daemon, or scheduler. Deployment must run it under an external supervisor.
- The anchor adapter must be independently administered and implement durable monotonic publication / compare-and-set semantics. The in-repository protocol cannot prove that an injected adapter is truly independent.
- Initial zero-checkpoint creation is a separate operator-controlled bootstrap action after verifying the empty audit database. Automatic initialization would let a compromised local host rewrite trust history.
- The default 300-second freshness budget is a policy default, not a universal SLO. Deployment can tune it through trusted host composition.
- Backup/restore automation, a concrete remote anchor provider, metrics export, alert routing, and deployment-specific scheduler manifests remain outstanding.
- Test fakes are not production anchors.

## Regression coverage added

- stale and future checkpoint rejection;
- checkpoint refresh without new events;
- checkpoint advancement after new audit events;
- missing-anchor bootstrap refusal;
- local history behind anchor rejection;
- production context rejection of stale checkpoints and invalid freshness policy.

Tests are added but were not executed in this environment. CI results must be reviewed before merge.
