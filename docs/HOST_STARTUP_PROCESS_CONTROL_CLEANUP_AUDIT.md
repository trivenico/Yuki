# Host startup interruption cleanup audit

**Review date:** 2026-11-28  
**Branch:** research/reference-projects-integration-assessment  
**Scope:** `scripts/run_web.py:create_app`, `ui/desktop/launch.py:_build_services`, and their resource-construction paths.

## Finding: process-control exceptions bypass host startup cleanup

**Severity:** Reliability / resource-lifecycle gap. Impact is limited to interrupted or terminating startup paths; this is not an authorization bypass.

### Evidence

The web host creates a `RuntimeResourceGroup` before constructing authentication, provider, agent, and API resources. Successfully created long-lived resources are transferred into that group. Its startup guard uses:

```python
except Exception:
    resources.close()
    raise
```

The desktop host follows the same exception boundary in `_build_services()`: it accumulates authentication, workflow, session, provider, task-runtime, and coordinator resources in `startup_resources`, but its cleanup guard also catches only `Exception`.

Python process-control exceptions such as `KeyboardInterrupt` and `SystemExit` inherit directly from `BaseException`, not `Exception`. If one occurs after one or more resources have been constructed but before the host has transferred ownership to its final runtime/lifecycle object, these guards are skipped. The host therefore does not invoke its explicit reverse-order cleanup hooks for the partially constructed resource set.

The API composition function itself already demonstrates the stronger pattern: `build_conversation_runtime()` catches `BaseException` during composition, attempts cleanup, and re-raises the original interruption. The outer web launcher has an earlier construction phase which remains outside that protection.

### Impact and limits

An interrupt during startup can leave provider clients, SQLite-owning services, or other closeable resources without their explicit cleanup hooks. In a normal process termination, the operating system will reclaim many process resources, so this does not establish a persistent external leak. It matters for embedded hosts, controlled restart flows, tests, and any startup interruption that does not immediately terminate the process.

This is distinct from the already documented API shutdown retryability finding: this issue concerns partial construction before ownership transfer, not failed cleanup after a runtime has been built.

### Recommended remediation

1. Change host startup guards to catch `BaseException`, run best-effort cleanup, and immediately re-raise the original exception.
2. Preserve cleanup failures as bounded secondary diagnostics or attach retained failed handles to the original exception; do not replace the startup exception with a cleanup exception.
3. Keep cleanup reverse-ordered and identity-deduplicated.
4. Add fault-injection tests that raise `KeyboardInterrupt` and `SystemExit` after provider creation and after later resource construction, asserting that every acquired resource receives a close attempt and the original exception is preserved.
5. Ensure any failed cleanup handles remain reachable for retry where the host can continue running.

### Verification boundary

This is a source-level finding from PR #1346 head `83ef131dfe2d01f1b0bac114ce158ab70b6a07bf`. No startup was interrupted, no provider was initialized, and no tests or production processes were run. The finding is documented only; no runtime remediation is included.
