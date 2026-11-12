# Phase 217 — Live worker approval wait and protected delivery

## Implemented

- Durable workers now build the exact central policy request from the registered skill and trusted owner/goal/task/claim context before invocation.
- When policy returns `REQUIRE_APPROVAL`, the worker persists an idempotent approval request in the durable gateway instead of immediately failing the task.
- The worker waits for the exact claim-bound credential while a heartbeat thread renews the task lease. It revalidates the lease throughout the wait and immediately before dispatch.
- The wait has a bounded configurable deadline (1–86,400 seconds). Expiry, lease loss, heartbeat failure, or invalid credential fails closed; no skill handler is dispatched without a valid one-shot approval.
- Added `DurableTaskEngine.get_active_claim_by_digest` and `validate_claim_digest`. These resolve and validate the worker identity for an active claim without returning or accepting the raw lease secret.
- Added broker digest-based delivery. The broker checks the approval request's claim digest against the active durable claim before and after storing the encrypted credential.
- Added `ApprovalDeliveryRouter`. It reads safe approval metadata, resolves the active worker from the task engine, and delivers only to that worker's registered broker. The API callback does not receive the worker's raw lease token.
- Added an end-to-end integration test that starts a worker, observes its persisted approval request, approves it, routes the credential by claim digest, and verifies the worker resumes and executes exactly once.

## Wiring

Configure `AuthenticatedCapabilityApprovalService(deliver_credential=router.deliver)` with the same gateway used by the worker runtime. Register one broker per worker identity with:
- the same approval gateway,
- a reviewed encryption adapter,
- the worker's existing lease validator,
- `validate_claim_digest=engine.validate_claim_digest`.

The worker runtime must use the same policy engine/gateway and a policy request factory that binds the trusted `SkillPolicyContext` fields, including `claim_token_sha256`. The worker must be constructed with its own broker's `resolver()`.

## Important limitations

- This is a synchronous worker wait, not a durable `awaiting_approval` task state. The worker process remains occupied while waiting; the task lease is kept alive. A process crash expires the lease, and the claim-bound approval request cannot authorize a reassigned generation.
- The approval request remains pending if the worker crashes; expiry/recovery and owner-visible stale-request cleanup are still needed. An approval after the claim expires fails delivery because the router cannot resolve an active matching claim.
- The approval UI must show a trusted, capability-generated operation preview. Current inbox metadata does not expose raw arguments or model-generated descriptions.
- Production still requires KMS-backed AEAD/key rotation, authenticated deployment configuration, route rate limits, service supervision/health alerts, multi-process crash/race tests, provider-side commit fencing/idempotency, and release certification.
- No local test execution or production readiness certification is claimed. CI must pass on the exact PR head before this phase is accepted.
