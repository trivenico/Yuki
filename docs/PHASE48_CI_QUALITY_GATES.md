# Phase 48 — CI quality gates

## Goal
Improve repeatable validation without claiming that a passing unit suite proves production readiness.

## Changes
The GitHub Actions workflow now:
- grants only `contents: read` to CI;
- cancels superseded runs for the same branch;
- caps each matrix job at 20 minutes;
- validates installed dependency consistency with `pip check`;
- compiles Python source with `compileall` before running unit tests;
- keeps the existing Ubuntu, Windows and macOS matrix on Python 3.11.

## Why not auto-merge?
CI results are evidence, not authorization to deploy. PR conflicts, behavior, secret handling, unsafe integrations and live inference still require separate review. Do not enable auto-merge or publish deployment credentials from this workflow.

## Remaining validation gaps
- Workflow must execute on GitHub and all matrix jobs must pass; this editing session cannot run the Actions jobs.
- Add a separate lint/type/security dependency scan only after selecting and pinning tools compatible with the project's current dependency policy.
- Test supported Python versions beyond 3.11 after the dependency and OS support matrix is defined.
- Run provider integration tests only with explicit opt-in and isolated test credentials; ordinary CI must not contact external model services.
- Production release still requires successful real inference, security review, installation test, data backup/restore and rollback rehearsal.
