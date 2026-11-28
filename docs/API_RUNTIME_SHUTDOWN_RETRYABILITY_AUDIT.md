# API runtime shutdown retryability audit

**Review date:** 2026-11-28  
**Branch:** research/reference-projects-integration-assessment  
**Scope:** `api/runtime.py`, `core/runtime_resources.py`, `core/conversation_application_runtime.py`, and the desktop lifecycle comparator.

## Finding: API shutdown permanently marks failed cleanup as complete

**Severity:** Reliability / resource-lifecycle defect; operational impact depends on the resource and host shutdown path.

### Evidence

`ConversationRuntime.close()` returns immediately when `_closed` is true. On its first call it sets `self._closed = True` before attempting to close either the conversation application or the transferred `RuntimeResourceGroup`. It catches ordinary cleanup errors, continues attempting the other resource, and then raises an incomplete-shutdown error. If either close operation fails, a subsequent call to `ConversationRuntime.close()` returns immediately because `_closed` is already true. The runtime therefore cannot retry failed cleanup through its public close method.

The transferred resource group has a related one-shot behavior. `RuntimeResourceGroup.close()` marks itself closed and clears its resource list before invoking resource close hooks. It continues after failures and raises an aggregate error, but failed resources are no longer retained for a retry. This is particularly relevant to providers or clients whose close operation can fail transiently.

The desktop lifecycle implementation provides a useful contrast: `DesktopServiceLifecycle.close()` records each successfully closed resource and retains failed hooks for a later retry.

### Impact and limits

A transient close failure can leave a provider/client/socket or other transferred resource open while the API runtime reports itself closed to later callers. The ASGI lifespan handler calls `self.close()` on shutdown; if cleanup raises, shutdown does not report completion, but the runtime's closed flag prevents an in-process retry from reaching the failed hooks. This is a lifecycle reliability and resource-retention defect, not evidence of remote code execution, credential disclosure, or an observed production leak.

The source review does not establish which concrete provider resources are passed through `owned_resources` in a deployed host. Impact therefore depends on the host's resource composition and whether a close hook can fail.

### Recommended remediation

1. Track shutdown as explicit states (for example, open, draining, closing, closed) rather than setting `_closed` before cleanup succeeds.
2. Retain failed resources and their close hooks; mark each resource complete only after its close operation succeeds.
3. Make `RuntimeResourceGroup.close()` retryable in the same manner as `DesktopServiceLifecycle`, while preserving reverse-order cleanup and attempting all resources.
4. Ensure concurrent/re-entrant close calls are serialized and cannot run the same close hook concurrently.
5. Preserve the first cleanup error while reporting all failed resource labels, without discarding retry state.
6. Add fault-injection tests where one resource fails once and succeeds on retry, another resource succeeds on the first pass, and repeated/concurrent shutdown calls remain safe.

## Verification boundary

This is a source-level finding from the branch head. No runtime code was changed, no local tests were run, and no production resource leak was observed.