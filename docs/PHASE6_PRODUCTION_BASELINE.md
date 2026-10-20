# Production engineering sequence — Phase 6 baseline

## Fixed in this branch

- Restored the missing approval-flow, manifest-dispatch, audit, pending-store, and bounded subprocess modules required by the checked-in approval-flow tests.
- Added the hardware inventory implementation and `/hardware` terminal command.
- Made the device inventory skill accept the workspace argument used by the registry without granting it filesystem scanning or device-probing behavior.
- Validate provider names before constructing adapters, so invalid provider configuration fails deterministically without touching local runtime initialization.
- Kept the approval boundary explicit in the runtime wiring test: the test grants approval only in its own test instance; production defaults remain approval-gated.

## CI hardening added

- Python 3.10, 3.11, and 3.12 test matrix.
- Compile-all gate and full unittest discovery.
- Least-privilege GitHub Actions permissions, bounded job duration, pip caching, and cancellation of superseded CI runs.
- Scheduled and PR-triggered `pip-audit` for declared runtime dependencies.

## Known gaps

- A green Linux Python matrix is not evidence of Windows/macOS support. OS-specific CI and real host testing are still needed.
- Hardware inventory is best-effort; NVIDIA uses `nvidia-smi`, Apple Silicon identification is heuristic, and AMD/Intel accelerator support is incomplete.
- Model fit is estimated from reported file size and RAM; context cache, runtime overhead, GPU offload, and concurrent workload can alter memory needs.
- The repository still needs a complete Ruff/mypy/coverage policy and baseline remediation; this increment does not claim that those quality gates pass.
- A dependency audit can fail if upstream advisories identify vulnerable pinned/transitive packages. Review and update dependencies rather than suppressing advisories.
- Production skill invocation must remain disabled until a verified OS sandbox and independent security review exist.

## Acceptance criteria

1. All Python matrix jobs pass on the same commit.
2. Dependency audit passes with no unreviewed known vulnerabilities.
3. CI failures are fixed at source; no skipped tests or softened security gates to manufacture green status.
4. Cross-platform and OS-sandbox release gates are tracked separately and not inferred from Linux unit tests.
