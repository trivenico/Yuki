# Code Readiness and Test-First Stabilization Plan

**Status:** baseline plan with an initial CI result recorded below. This is not a claim of production acceptance.

## Objective

Make the repository easier to validate and debug before adding more features. Prefer small, reviewable changes with reproducible tests and a clear failure signal. Do not describe a component as production-ready merely because its module exists or a pull-request workflow is green.

## Phase A — Freeze a reproducible baseline

1. Start from a clean checkout of the exact commit under test.
2. Use a supported Python version (the README currently documents Python 3.10+) and create an isolated virtual environment.
3. Install the core requirements with `python -m pip install -r requirements.txt`.
4. Run the complete portable unit-test suite:
   ```bash
   python -m unittest discover -s tests -v
   ```
5. Record the commit SHA, Python/OS versions, command, exit code, test count, and full log. Do not mix optional hardware/network integration tests into the core suite.
6. If the baseline fails, stop feature work and create a minimal reproduction for the first failure. Fix root causes, not assertions or tests merely to make CI green.

## Phase B — Make failures easy to isolate

Classify each failure as one of:
- import/packaging or configuration;
- unit behavior or regression;
- filesystem/path/permissions;
- authorization/security policy;
- provider/network integration;
- optional hardware (audio, browser, Docker/Podman);
- platform-specific behavior;
- flaky or order-dependent test.

For every defect, capture:
- exact failing command and test name;
- expected vs actual behavior;
- minimal reproduction and relevant traceback/log;
- severity and security/data-loss impact;
- fix and regression test;
- rerun result on the exact resulting commit.

Use this order when debugging: reproduce, isolate, fix, add regression coverage, run the focused test, run the full suite, then run relevant cross-platform and integration checks.

## Phase C — Required automated checks

- Full core unit-test suite on supported Python/OS versions.
- Static checks and dependency/security audit where configured by the repository.
- Configuration and startup smoke tests with no provider credentials.
- Authorization tests proving denied actions remain denied and approvals are scoped correctly.
- Filesystem boundary, symlink, timeout, cleanup, and failure-path tests for file/worker operations.
- Optional integration tests are explicitly marked and must report **skipped** separately from **passed**.
- Preserve logs/artifacts for failures without including credentials, tokens, private user content, or secret environment values.

## Phase D — Runtime acceptance

A passing unit suite does not prove deployment readiness. Before production use, separately verify:
- installation from a clean checkout and documented startup/shutdown;
- supported OS/Python versions and optional dependency behavior;
- authentication/session and local database permissions;
- secret handling, logging, backup/restore, and recovery;
- resource/time/output limits and failure behavior;
- real rootless container isolation for untrusted candidate tests;
- independent security review for approval and self-upgrade paths.

## Self-upgrade safety invariant

Until independent security review and real isolated-worker acceptance are recorded, keep autonomous promotion, merge, install, restart, and production deployment disabled. Candidate code must never run on the host merely because a test double or ordinary CI job passed. Preserve human approval for privileged changes.

## Completion criteria

This baseline is complete only when a maintainer attaches a test report tied to an exact commit and marks each required check as passed, failed, or skipped with a reason. Production readiness is a separate decision and requires runtime evidence and review; it is not a percentage inferred from the number of modules or green workflows.


## Initial CI evidence (2026-10-26)

The initial PR head `96bbaab21c09477957ef00809633750ef87b880b` completed all 15 PR-triggered workflow runs successfully. The `Yuki quality gates` workflow completed on all three of these runners using Python 3.11:

| Runner | Unit tests | Result |
|---|---:|---|
| Ubuntu latest | 605 | Passed |
| macOS latest | 605 | Passed |
| Windows latest | 605 | Passed |

The exact command was `python -m unittest discover -s tests -v`. The quality-gate job also passed dependency consistency validation, Python source compilation, and its project/core-import diagnostic. Logs note that the diagnostic does not validate voice hardware, live AI providers, or browser automation.

This is a useful **core-suite baseline**, not a complete runtime acceptance report. It does not establish rootless Podman isolation, production deployment readiness, external integration behavior, or independent security approval. Because this PR updates the plan, its new head must receive its own CI run; the evidence above refers only to the exact SHA stated here.
