# Conversation Generation Lease Release Failure Audit

## Scope

Reviewed `core/conversation_service.py`, `core/conversation_store.py`, and `tests/test_conversation_service.py` on PR #1346 branch `research/reference-projects-integration-assessment`.

Source inspection only. No runtime code or tests changed or executed.

## Confirmed finding: lease-release failure can override a completed reply or its original exception

### Evidence

1. `ConversationService.reply()` acquires a database generation lease and starts a heartbeat.
2. Its outer `finally` first calls `heartbeat.stop()`, then calls `store.release_generation_lease(...)`.
3. The release call is not protected by a secondary cleanup handler. Any database exception propagates out of the `finally` block.
4. In Python, an exception raised from `finally` replaces a return value and can replace an exception already propagating from the protected body.
5. The lease store's release is a conditional delete matching conversation ID, owner ID, and lease token. If release fails due to a transient database error, the row remains until its recorded expiry unless another operation removes/replaces it.
6. Lease acquisition rejects a still-live lease. The configured service TTL is bounded to 5–3600 seconds; the default is 180 seconds. A later request may therefore be rejected until expiry when cleanup cannot delete the row.
7. Token matching prevents the old worker's eventual release from deleting a replacement worker's lease. Renewal also requires the same live token and cannot revive an expired lease.

### Impact

If the model answer and assistant message have already been persisted but lease release fails, the caller receives a database exception instead of the successful reply result. A retry with the same request ID can replay the already-persisted answer, but a different request may be temporarily blocked by the still-live lease until expiry.

If another exception was already propagating from generation, a release exception can obscure the original failure and make diagnosis harder.

This is a bounded availability and error-reporting defect. The lease is not permanent: it expires, and token fencing prevents stale cleanup from deleting a newer lease. No cross-owner access or duplicate execution is established by this finding.

### Additional cleanup-path observation

The heartbeat thread's `stop()` sets its stop event and joins the thread. The heartbeat may be inside a synchronous store renewal call when stop is requested. The SQLite connection configures a 10-second busy timeout, so a lock wait can delay reply cleanup; this is bounded at the SQLite busy-handler level, not evidence of an indefinitely hung thread. No separate defect is asserted here.

The heartbeat-start failure path also calls release directly; a release exception there can replace the original thread-start exception.

### Recommended remediation

- Preserve the primary reply result/exception when lease cleanup fails; capture and report cleanup failure separately.
- Record a structured cleanup event/metric containing conversation ID (or a safe correlation ID), token fingerprint, and failure class, without logging the raw lease token.
- Treat a failed release as a temporary lease-retention condition; rely on expiry and token fencing rather than attempting unsafe unconditional deletion.
- Consider a bounded join for heartbeat shutdown with an explicit policy for a still-running renewal thread; do not release/close shared resources while that thread can still use them.
- Add fault-injection tests:
  - release raises after a successful assistant persistence: reply outcome remains recoverable and retry replays the answer;
  - release raises while another exception is propagating: primary failure remains visible and cleanup failure is separately observable;
  - release failure leaves the lease unavailable until expiry, then a new token can acquire it;
  - old-token release after replacement does not delete the replacement lease;
  - heartbeat stop during a blocked renewal follows the documented timeout behavior.

## Protections verified

- Lease release is conditional on conversation, owner, and unique token.
- Lease renewal cannot revive an expired lease.
- A stale worker cannot persist an assistant message without a currently live matching lease token.
- Expired leases can be reclaimed; old tokens cannot release the replacement lease.
- Lease TTL is constrained to a finite range.

## Severity and confidence

- **Classification:** reliability / cleanup error handling / temporary availability.
- **Confidence:** high; the unguarded release call is in a `finally` block and store operations can raise database exceptions.
- **Impact window:** at most the remaining lease TTL for ordinary expiry behavior, subject to database availability.
- **Production occurrence:** not established.

## Validation status

- Source inspection only.
- No tests executed.
- No runtime code changed.
