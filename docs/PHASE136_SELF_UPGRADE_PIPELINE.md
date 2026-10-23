# Phase 136 — Self-upgrade proposal boundary

## Scope
This phase adds a proposal builder only. It does not modify files, execute tests,
create branches automatically, run shell commands, or promote changes.

## Safety properties
- Every target path must be explicitly allow-listed.
- Sensitive, generated, credential-like and repository metadata paths are excluded.
- Proposals are limited to eight files, 256 KiB per file and 1 MiB total.
- Existing content digests are required and proposed content is SHA-256 fingerprinted.
- Proposal IDs are deterministic for identical manifests.
- Review and tests are always required; promotion is always disallowed by this component.

## Follow-up integration
A trusted coordinator may connect this proposal object to the existing
`automation.approved_workspace_changes.ApprovedWorkspaceChanges` and
`ApprovedTestRunner` services. Those services must remain separately authorized,
must use a trusted authenticated principal, must require digest-bound approval,
and must execute only allow-listed test modules. Do not treat a successful
proposal build as approval or test success.

This phase is not an OS sandbox. Running tests over untrusted proposed code can
execute that code, so test execution must happen in a disposable, appropriately
isolated environment with no production credentials or network privileges.

## Integration gate
The proposal builder depends on the guarded workspace-change and read-tool contracts. CI must run against the current `main` baseline, including `core.guarded_tool_runtime`; a rerun of an old merge ref is not evidence that the current dependency chain is healthy.
