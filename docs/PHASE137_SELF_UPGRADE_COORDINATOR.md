# Phase 137 — Self-upgrade coordinator integration

The coordinator stages approved workspace changes in a disposable copy, runs only explicitly allow-listed test modules, and never promotes changes back to the source workspace.

**Fail-closed default:** host-side test execution is disabled unless `allow_host_test_execution=True` is explicitly configured. That switch is for controlled local development with trusted proposals only; it is not a sandbox and must not be enabled for untrusted proposals.

**Production security boundary:** before validating untrusted proposals, integrate a dedicated low-privilege isolated worker with no production credentials, restricted network access, CPU/memory/process limits, an external timeout supervisor, and auditable artifact handoff. A temporary directory, subprocess, allow-list, or approval callback alone is not an operating-system security boundary.

All proposal generation, workspace mutation, test execution, approvals, and audit events are separate gates. Automatic promotion remains disabled. A successful test run is not approval to deploy.
