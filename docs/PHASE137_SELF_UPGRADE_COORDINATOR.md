# Phase 137 — Self-upgrade coordinator integration

This coordinator stages approved workspace changes in a disposable copy and runs only explicitly allow-listed test modules. It never promotes changes back to the source workspace.

**Security boundary:** a temporary directory is not an operating-system sandbox. Running Python tests can execute arbitrary code. Do not use this coordinator for untrusted proposals unless it runs in a dedicated low-privilege worker with no production credentials, restricted network access, resource limits, and external supervision.

All proposal generation, workspace mutation, test execution, approvals, and audit events are separate gates. Automatic promotion remains disabled. A successful test run is not approval to deploy.
