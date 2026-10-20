# Phase 35 — Approval-to-Execution Integrity

## Audit finding
The ReAct executor made a detached copy of model-supplied arguments for the approval callback but then passed the original arguments to the tool handler. A callback could mutate the reviewed copy, and the system did not have an explicit invariant that execution uses the exact approved payload.

## Fix pushed
- ReAct now canonicalizes validated arguments to a bounded JSON snapshot before approval.
- The approval callback receives a detached object parsed from that snapshot.
- Tool execution receives a fresh object parsed from the same snapshot, not the model-owned object or callback-owned object.
- Added a regression test in which the approval callback deliberately mutates its arguments and verified the handler still receives the originally reviewed payload.

## Additional audit findings
- `yuki_approvals.py` documents that approvals are in-memory and decisions do not execute actions. Approval requests are not yet cryptographically bound to tool name, risk, argument hash, expiry, and one-time consumption.
- `core.agent.YukiCoreAgent` calls the legacy `PermissionGate` and then invokes `skill.execute` directly. This is a distinct authorization path from the ReAct/MCP helper and needs a facade-level integration before system-wide enforcement can be claimed.
- `PluginLoader` explicitly imports Python modules in-process. A trust list limits discovery but is not a sandbox; untrusted generated code must not be imported into the main process.

## Scope / known gap
This phase closes the ReAct approval/execution snapshot gap. It does not complete system-wide authorization, approval persistence, canonical configuration migration, or plugin isolation.

## Verification
Phase 34 CI run #1240 initially failed on an incorrect regression-test expectation. The test was corrected and a new CI run is required for the latest Phase 35 head. Do not describe Phase 35 as verified until that run passes.

## Remaining production gates
1. Introduce one invocation facade used by ReAct, MCP, `YukiCoreAgent`, plugins, CLI/TUI, daemon and scheduler.
2. Bind approval records to action/tool, risk, canonical argument hash, requester, expiry, and atomic single-use consumption.
3. Isolate untrusted plugins and generated skills in a least-privilege subprocess/container; do not treat import allowlists as a sandbox.
4. Finish canonical runtime configuration and reversible YAML migration.
5. Run current-head CI, type/lint checks, dependency/secret scanning, integration tests, and independent security review.

## Readiness estimate
Remain at 40% (engineering estimate, medium-low confidence). This is a targeted security fix, not a system-wide production certification.