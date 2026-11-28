# Composite provider partial-construction and cleanup retry audit

Date: 2026-11-28
Repository: trivenico/Yuki
Reviewed revision: 9a936edb4f2d5049a9b9019c18f3000a067db644

## Findings

### P2 — Hybrid factory leaks the local child when cloud construction fails

In `providers/factory.py:create_provider()`, the hybrid branch constructs an `OllamaProvider` first, then conditionally constructs an `OpenAICompatibleProvider`. The cloud constructor performs validation that can raise, including model validation, API-key environment-variable-name validation, timeout validation, and trusted endpoint validation.

If that second construction raises, the already-created local provider is abandoned without calling its `close()` method. The current Ollama adapter does not appear to allocate a persistent socket in its constructor, which limits immediate impact today, but the factory contract supports provider implementations with owned resources and makes this a latent resource leak as adapters evolve or are injected.

The same partial-construction pattern should be considered whenever a composite factory constructs multiple children before handing ownership to a wrapper.

**Remediation**
- Construct child providers under an explicit ownership guard.
- If any later child construction or composite-wrapper construction fails, close every successfully constructed child exactly once.
- Preserve the original construction exception; if cleanup also fails, retain both errors (for example, an exception group or a chained aggregate) rather than replacing the primary cause.
- Add a deterministic test that makes cloud construction fail after local construction and asserts local cleanup runs exactly once.

### P2 — Hybrid close retries already-successful child closes after a sibling failure

In `providers/hybrid.py:HybridProvider.close()`, owned children are closed sequentially. Exceptions are accumulated, but the wrapper sets `_closed = True` only when no child close failed. If any child fails, a later `close()` retry iterates over all children again.

For example:
1. The cloud child closes successfully.
2. The local child raises from `close()`.
3. The wrapper raises and remains `_closed == False`.
4. A retry calls `close()` on both children again, including the cloud child that already closed successfully.

This can double-close a child whose close operation is not idempotent. It can also repeat external side effects or raise secondary errors that obscure the remaining cleanup state. The existing manager close retry behavior relies on retryable cleanup, so the composite wrapper should track per-child completion rather than retry every child indiscriminately.

**Remediation**
- Track close completion independently for each owned child (or remove successfully closed children from a pending-close collection).
- Mark each child complete immediately after its close succeeds.
- On retry, invoke close only for children still pending.
- Preserve failed child state for retry and make concurrent/reentrant close behavior explicit.
- Add tests for cloud-success/local-failure and local-success/cloud-failure, asserting the successful child's close count remains exactly one across retries.
- Retain the identity-based de-duplication behavior when both child references point to the same object.

## Scope and limitations

This is source inspection of `providers/factory.py`, `providers/hybrid.py`, and `providers/openai_compatible.py` at the reviewed revision. No provider construction failure was injected and no runtime tests were executed. The local Ollama implementation currently has no explicit `close()` method, so the partial-construction leak's immediate resource impact is limited for that concrete child today; the ownership defect remains relevant to future or custom child providers. The retry double-close behavior is directly represented by the current control flow whenever a child close succeeds and a sibling close fails.

## Acceptance criteria

- A failure constructing any later hybrid child closes all earlier successfully constructed children exactly once.
- A failed hybrid close retains only children whose close operation failed; subsequent retries do not call already-successful child closes again.
- Cleanup failures do not erase the primary construction/close failure.
- Repeated close after all children have closed is harmless.
- Shared child identity is closed at most once.
