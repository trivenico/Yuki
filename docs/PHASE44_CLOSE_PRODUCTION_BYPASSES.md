# Phase 44 — Close production invocation bypasses

## Audit findings

Phase 43 introduced a production registration gate, but two important weaknesses remained:

1. The gate trusted a mutable `sandbox_enforced = True` attribute. A test double or incorrectly configured executor could satisfy the check without enforcing OS isolation.
2. `YukiCoreAgent.run_skill()` still executed legacy registry skills in-process after ordinary human approval. The manifest dispatcher gate did not protect this separate runtime path.

## Changes

- Production `SkillDispatcher.register_subprocess()` now rejects registration unconditionally until a reviewed, verified OS-sandbox backend is shipped. A self-declared attribute is not accepted as proof.
- `YukiCoreAgent.run_skill()` now fails closed in `YUKI_ENV=production`, preventing the legacy in-process path from executing skills while runtime migration and sandboxing remain incomplete.
- Regression coverage checks that fake sandbox declarations are rejected and legacy in-process invocation is blocked in production.

## Compatibility and impact

This is a security fail-closed change. Setting `YUKI_ENV=production` will intentionally disable skill execution until a real verified backend and a universal controlled invocation path are implemented. Development behavior remains unchanged.

## Remaining gaps

- Implement and independently validate actual Windows, macOS, and Linux sandbox adapters.
- Integrate `InvocationFacade` into the runtime and provide versioned manifest adapters for built-in skills.
- Ensure every mutation-capable tool and plugin path is subject to the same authorization policy.
- Add adversarial and cross-platform integration tests, dependency/security scans, restart/recovery tests, and independent security review.

A passing unit-test workflow does not demonstrate that OS sandboxing exists or that Yuki is production-ready.
