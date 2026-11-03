# Phase 138 — cross-version installation validation

Base: `main` at `dd9c88b524bf6e891ce0e92f2324ca4184ab3ae6`.

## Changes
- Expand the required quality-gate matrix from Python 3.11 only to Python 3.10, 3.11 and 3.12 on Ubuntu, Windows and macOS.
- Correct Unix and Windows installer warnings: Yuki does have a declared third-party runtime dependency (PyYAML), and the required dependency-install step remains mandatory even if pip self-upgrade fails.

## Acceptance
- All nine OS/Python combinations must install `requirements.txt`, pass `pip check`, compile sources and pass the complete unittest discovery suite.
- Any compatibility failure is a release blocker and must be fixed at source with a regression test; do not skip or weaken the failing test.
- Keep Python support claims aligned with the matrix that actually passes.
- Confirm installer failure propagation for dependency installation and tests on real clean target hosts before release. CI matrix coverage is not a substitute for interactive installer acceptance testing.

## Out of scope
- This does not implement the missing desktop application, production-grade model tool loop, encrypted backups or security certification.
