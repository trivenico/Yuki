# Phase 36 — Legacy Skill Authorization Integration

## Audit finding
`YukiCoreAgent.run_skill()` used a separate `PermissionGate` path and invoked `skill.execute()` directly. That created policy drift between the legacy skill path and the shared ReAct/MCP authorization helper.

## Fix pushed
- Routed `YukiCoreAgent.run_skill()` through `authorize_tool_call`, preserving the conservative behavior that every skill invocation requires explicit approval.
- Canonicalized the bounded skill argument into a JSON snapshot before authorization and executes the value reconstructed from that snapshot.
- Rejects non-text and over-500-character arguments before prompting for approval.
- Added integration tests for successful approval/execution, approval metadata, and oversized argument rejection.

## Scope / known gap
This integrates the legacy agent's public `run_skill()` method with the shared policy. It does not prove that all callers use this method rather than invoking skill objects directly. Plugin Python imports remain in-process and are not a sandbox; durable, hash-bound, expiring, one-time approval records remain outstanding.

## Verification
Phase 35 CI run #1246 passed on commit `164c607c1dfbaf94e04db8ea1268cee9f9d7628f`. Phase 36 commits are newer and require a fresh CI run on the latest head.

## Remaining production gates
1. Inventory and eliminate direct skill/plugin handler calls that bypass the agent or common invocation facade.
2. Bind approval decisions to exact action, risk, canonical payload hash, expiry, and atomic one-time consumption.
3. Isolate untrusted plugins/generated skills outside the main process with least privilege.
4. Complete canonical runtime configuration and reversible YAML migration.
5. Run current-head CI, type/lint checks, dependency/secret scans, integration tests, and independent security review.

## Readiness estimate
Remain at 40% (engineering estimate, medium-low confidence). This phase improves legacy-path consistency but is not a system-wide production certification.