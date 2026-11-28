# Desktop shutdown revocation-before-teardown ordering audit

**Status:** Confirmed lifecycle defect; documentation-only audit.
**Scope:** `ui/desktop/lifecycle.py`, `ui/desktop/launch.py`, and `tests/test_desktop_lifecycle.py`.
**Branch:** `research/reference-projects-integration-assessment`.

## Finding

`DesktopServiceLifecycle.close(before_close=...)` records an exception from the pre-close callback but continues closing all registered resources. It only raises `DesktopShutdownError` after the resource loop.

The production desktop launcher supplies session revocation as this callback through `_shutdown_desktop_services`. The callback invokes `controller.logout()` (or `sessions.logout()`). The actual desktop service composition registers the authentication store, workflow store, session manager, provider, task runtime, and specialist coordinator in the lifecycle. Reverse-order cleanup therefore closes the coordinator, task runtime, provider, session manager, workflow store, and authentication store even if revocation failed.

This is a dependency-ordering defect: revocation may need the authentication and workflow stores, but the lifecycle can close those dependencies after revocation has failed. The lifecycle retains `_before_close_complete = False`, so a later close retries the callback, but it does not retain the resources whose close already succeeded. The retry can therefore run against closed dependencies.

The existing test `test_session_revocation_runs_before_resources_and_failure_does_not_skip_cleanup` explicitly asserts that the resource is closed after the revocation callback fails, then treats a later callback success as sufficient to finish shutdown. That test encodes the unsafe ordering rather than detecting it.

## Evidence

- `ui/desktop/lifecycle.py`: `close()` attempts `before_close`, appends callback errors, then unconditionally enters the reverse resource-close loop. `_before_close_complete` remains false after callback failure, but successfully closed resource IDs are recorded and skipped on retry.
- `ui/desktop/launch.py`: `_shutdown_desktop_services()` passes session logout as `before_close`; `_build_services()` creates the auth/workflow stores and registers them with the lifecycle alongside the session manager, provider, task runtime, and coordinator.
- `tests/test_desktop_lifecycle.py`: the revocation-failure test expects events `["revoke", "resource"]`, then expects retry to invoke only the callback because the resource is already considered closed.

## Impact and boundaries

A transient or operational failure during session revocation can be followed by teardown of the authentication and workflow dependencies needed to complete revocation. A subsequent retry is not guaranteed to be able to complete the original security cleanup. This weakens the intended fail-closed shutdown guarantee.

This is a lifecycle correctness/security-hardening issue. The source establishes the unsafe ordering; it does not establish that a production user session was left active or that this path has been exploited.

## Recommended remediation

1. Treat successful pre-close revocation as a hard prerequisite for dependency teardown. If it fails, preserve all registered resources open and raise immediately (or record the failure and skip the resource loop).
2. Keep the lifecycle retryable: retry the failed pre-close callback on the next close attempt, then close resources only after it succeeds.
3. Add a test with a revocation callback that accesses a resource, fails once, then succeeds. Assert the resource remains open after the first attempt and is closed only after the successful retry.
4. Keep the existing per-resource retry behavior for failures that occur after revocation succeeds.
5. Ensure startup-failure cleanup follows the same security ordering when an authenticated session has been established.

## Validation

Source and existing test assertions were inspected through GitHub. No runtime code or tests were changed, and no tests were executed. This audit does not claim a production incident or exploit.
