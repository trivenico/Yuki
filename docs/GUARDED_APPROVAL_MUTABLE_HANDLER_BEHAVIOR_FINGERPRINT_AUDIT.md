# Guarded Tool Approval: Mutable Handler Behavior Is Not Bound to Approval Fingerprint

- **Severity:** Medium
- **Area:** Guarded execution / exact-action approval
- **Status:** Confirmed by source inspection and an existing regression characterization test
- **Scope:** `core/tool_action.py`, `core/guarded_tool_runtime.py`, `core/guarded_approval_executor.py`, `core/approval_composition.py`, `tests/test_tool_action_approvals.py`

## Finding

The approval envelope binds a tool invocation to `implementation_identity(handler)`. That function fingerprints the Python code object plus module and qualified name, but explicitly excludes closure-cell values, mutable globals, instance state, imported dependencies, and runtime environment.

The guarded runtime recomputes the same fingerprint immediately before dispatch. This detects code-object replacement, but it does not detect a behavior change caused by mutation of captured or ambient state when the code object remains unchanged.

The existing test `test_code_fingerprint_does_not_attest_mutable_closure_state` demonstrates the issue: two closures created from the same factory with different captured prefixes produce identical implementation fingerprints despite returning different behavior.

The trusted composition root accepts arbitrary callable handlers and does not reject closures, callable instances with mutable state, or other handlers whose effective behavior depends on mutable ambient state. Consequently, a user can confirm an exact action while the semantic behavior of the registered handler can change before dispatch without invalidating the approval fingerprint.

This is not a grant replay or token-consumption bypass. It is a gap in the claim that the approval binds to the exact handler implementation.

## Impact

For consequential registered tools that depend on mutable closure/global/instance state, approval may authorize arguments and a code object while failing to bind the effective operation performed at dispatch. A state change between confirmation and execution can therefore alter the operation without changing the fingerprint checked by the runtime.

The risk is conditional on the host registering handlers with mutable behavior. The implementation documentation already warns against this, but the composition API does not enforce the restriction.

## Recommended remediation

1. Prefer immutable, versioned handler implementations whose behavior does not depend on mutable closure or ambient state.
2. For callable objects, require an explicit immutable implementation/version identity supplied by trusted composition, rather than treating the Python code object as a complete behavior fingerprint.
3. Where state legitimately affects operation semantics, include a canonical, immutable snapshot or version of that state in the approval envelope and revalidate it immediately before dispatch.
4. Enforce safe handler registration in the composition root, or make the limitation an explicit documented trust requirement for every host-provided handler.
5. Add regression tests that mutate closure cells, globals, and callable-instance state after approval and verify that dispatch is denied when those values are part of the approved operation.

## Evidence and limitations

- `implementation_identity()` documents the excluded state and fingerprints only code/module/qualified name.
- `GuardedToolRuntime.invoke_detailed()` compares the same fingerprint before authorization and dispatch.
- `build_approval_services()` accepts a mapping of arbitrary callables and passes it to `GuardedApprovalExecutor`.
- `test_code_fingerprint_does_not_attest_mutable_closure_state` confirms distinct captured behavior can share the same fingerprint.

Source-review finding only. No tests, CI, or runtime experiments were executed for this audit pass.
