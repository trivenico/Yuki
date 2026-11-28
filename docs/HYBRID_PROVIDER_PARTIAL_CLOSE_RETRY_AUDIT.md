# Hybrid provider partial-close retry audit

Date: 2026-11-28
Repository: trivenico/Yuki
Reviewed revision: 5f5140e0d3c321a8f21f6404ea00a94f18df83bf

## Finding

**P2 — Retrying a partially failed hybrid-provider close repeats cleanup on children that already closed successfully.**

HybridProvider.close() owns child lifecycle when owns_providers=True. It serializes calls with _close_lock, returns immediately only when the wrapper's _closed flag is true, and otherwise iterates over the cloud and local children. It attempts every child close and accumulates exceptions. If any child close raises, the method raises a wrapper RuntimeError without setting _closed=True.

That means the wrapper does not retain which child closes completed successfully.

### Concrete failure sequence

1. A manager owns a hybrid adapter whose cloud and local child providers both implement resource-releasing close() methods.
2. HybridProvider.close() successfully closes the cloud child.
3. The local child's close() raises (for example, a transient transport/client shutdown failure).
4. The wrapper raises and remains _closed=False.
5. ProviderManager.close() retains the hybrid adapter after its close failure and intentionally permits a later close() retry.
6. On retry, HybridProvider.close() invokes close() on both children again, including the cloud child whose cleanup already succeeded.

A successfully closed child may therefore receive duplicate cleanup. For non-idempotent child implementations this can cause spurious errors, invalid state transitions, or repeated teardown side effects. It can also make the manager's otherwise retryable shutdown depend on every child close implementation tolerating duplicate calls.

The same behavior applies if the local child closes successfully and the cloud child fails, and to any repeated close attempt where one child succeeds while another continues failing.

## Why current ownership and retry behavior do not prevent it

- _close_lock serializes concurrent wrapper close calls, but it does not record per-child completion.
- _closed is a single all-or-nothing flag set only after every child close succeeds.
- ProviderManager.close() intentionally retains adapters whose close() raised so callers can retry cleanup; this makes the hybrid wrapper's missing progress tracking observable.
- The hybrid wrapper deduplicates identical child object references within one call, but that does not prevent the same child from being closed again on a later call.

## Recommended remediation

Track cleanup completion independently for each owned child:

1. At construction, initialize a private set (or explicit state fields) for child identities whose close completed successfully.
2. Under _close_lock, skip children already recorded as successfully closed.
3. After a child's close() returns successfully, record that child as closed immediately, before attempting the next child.
4. Preserve failed children as pending so a later wrapper close retries only those children.
5. Mark the wrapper _closed=True only after all owned children have either completed cleanup or required no close method.
6. Keep the existing behavior of attempting all pending children and reporting aggregated cleanup failure, while preserving the first failure as the cause.
7. Add deterministic tests with two fake owned children: make the first child's close succeed and the second fail once; call wrapper close again after removing the failure; assert the first child was called once and the second twice. Also cover reversed child order, aliased child references, child objects without close(), and repeated successful wrapper close.

A per-child state set should use object identity, not equality, so provider-defined __eq__ implementations cannot conflate distinct resources. Alternatively, retain the actual child references in a small internal completion structure to avoid relying on reusable numeric object IDs.

## Scope and limitations

This finding is based on source inspection of providers/hybrid.py, providers/factory.py, and yuki_providers.py at the reviewed revision. The factory creates hybrid providers with owns_providers=True, and the manager retries adapter cleanup after failures. No runtime reproduction or tests were executed. The impact depends on owned child providers having non-idempotent or failure-sensitive close implementations; the current Ollama and OpenAI-compatible adapters are largely stateless and do not override close(), but HybridProvider is a generic wrapper accepting arbitrary compatible child objects and explicitly owns their lifecycle when requested.

## Acceptance criteria

- A child whose close completed successfully is never closed again on a later hybrid close retry.
- A child whose close failed remains eligible for retry.
- All pending children are attempted even when one child's close fails.
- The wrapper becomes closed only after every owned child's cleanup has completed.
- Deterministic unit tests prove partial success is remembered across retries and duplicate child references are closed at most once.
