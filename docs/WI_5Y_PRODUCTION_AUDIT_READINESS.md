# WI-5Y — Production audit readiness endpoint

## Problem

WI-5W exposed ProductionSecurityContext.verify_audit_integrity() for host readiness checks, but the API runtime did not expose a production-only readiness route that deployment probes could call. A host could forget to wire the hook into its orchestration checks.

## Changes

- Carry the validated ProductionSecurityContext into strict ConversationRuntime instances.
- Add GET /health/ready only when central production authorization is enabled.
- Re-verify the durable audit chain and external checkpoint on every readiness request.
- Return HTTP 200 with a minimal authorization-audit success signal, or HTTP 503 with a generic failure signal. Do not expose exception details.
- Mark responses no-store to prevent caches from preserving a stale readiness result.
- Add ASGI tests for healthy readiness and fail-closed behavior after audit tampering.

## Scope

This is an audit-integrity readiness signal, not a complete application health certification. Deployment must separately monitor policy, identity, storage, provider, and external anchor availability. The route is only available when the strict production runtime is composed; local/legacy runtimes retain their existing behavior.

## Limitations

- The route performs verification on each probe. Deployments should choose a probe cadence appropriate for their audit database size and configured checkpoint freshness budget.
- It does not automatically publish checkpoints; WI-5X publisher remains a separately supervised scheduled operation.
- No deployment manifest, load-balancer configuration, or external monitoring rule is created here.
- Tests are added but were not executed in this environment.
