# WI-5W — Production audit-anchor startup and readiness gate

## Finding

WI-5V added independent checkpoint publication and verification primitives, but ProductionSecurityContext only required a durable local SQLite writer. A deployment could still construct a strict production context without any independent anchor and therefore never verify whether its local history had been truncated or rewritten.

## Changes

- ProductionSecurityContext now requires an AuthorizationAuditAnchor implementation.
- Context construction verifies the local audit chain against the externally retained checkpoint and refuses construction when the anchor is missing, unavailable, malformed, or mismatched.
- Expose verify_audit_integrity() for repeated readiness checks and post-restore validation.
- Update production bootstrap documentation to require host-owned checkpoint scheduling, alerting, and readiness integration.
- Add regression coverage for missing/mismatched anchors and integrity corruption after context construction.

## Deployment boundary

The stock web launcher intentionally does not create an external anchor service. The deployment-owned bootstrap must inject a real independently administered anchor alongside the authorization gateway. In-memory anchors are test fixtures only.

A scheduler must call SQLiteAuthorizationAuditLog.publish_checkpoint(anchor) on a monitored interval. Production readiness should periodically call ProductionSecurityContext.verify_audit_integrity() and after every backup/restore. Alert on checkpoint publication lag. Never reset or roll back the external anchor to match an older restored database.

## Limitations

This PR enforces verification at context construction and provides a repeatable readiness hook. It does not deploy a remote anchor service, create a scheduler, configure monitoring, or automatically wire a readiness endpoint. Those remain deployment integration responsibilities. Tests were added but not executed in this environment.
