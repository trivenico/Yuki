# Phase 44 — Repository-wide audit findings

## Scope and confidence

This audit reads the default branch through the GitHub repository connector and inspects the primary runtime, provider factory/registry, config loader, dependency manifest, CI workflow, README and changed-file lists for PRs 187–189. It is a **targeted remote audit**, not a claim that every repository file or test was executed locally. The static audit tool added in this phase scans all Python files in a checked-out worktree without importing or executing them.

## Confirmed integration gaps

1. **New model-routing, Hugging Face scouting and evaluation code is not on `main`.** PRs #187, #188 and #189 add files absent from the default branch. Their existence does not mean the main runtime can use them.
2. **Provider registries are separate.** Main's `yuki_providers.py` reports only `mock` as implemented. `providers/factory.py` separately supports `mock`, `ollama` and `hybrid`. The application must use one authoritative capability source or tests/UI can disagree about what is selectable.
3. **CI currently runs unit tests only.** `.github/workflows/tests.yml` installs `requirements.txt` and runs unittest discovery on Python 3.11 across Ubuntu, Windows and macOS. It does not explicitly compile all Python files or run a static audit.
4. **Dependency declarations are minimal.** `requirements.txt` only includes PyYAML. This is appropriate for the core but optional features need explicit extras/install documentation and CI coverage where practical.
5. **The evaluation gate is only a baseline.** Deterministic substring assertions and observed latency are not semantic benchmarking or a hard cancellation mechanism. Adapter deadlines, representative task datasets and isolated candidate trials remain required.
6. **Hugging Face discovery is not an in-app alert system yet.** PR #188 adds a pull-based CLI scout; persistent deduplication, scheduled in-Yuki execution, release diffs and user-facing notification integration remain follow-up work.
7. **No verified green CI result is available from this audit.** Do not merge based solely on the presence of test files or PR descriptions.

## Audit tool

`python -m tools.repo_audit .` parses Python source via `ast` without importing it. Syntax/read failures are blocking. High-risk API patterns are advisory by default because some legacy modules may intentionally use them; `--strict-high` can be used during focused remediation. It flags dynamic `eval`/`exec`, pickle deserialization, `subprocess` shell usage/timeouts, and selected network calls without timeouts. Static findings require human review and can include false positives.

## Required merge gates

- Python syntax compile and static audit complete.
- `python -m unittest discover -s tests -v` passes on the supported OS matrix.
- Every PR has a clear dependency/base-branch plan; avoid parallel feature PRs based on stale `main` if one depends on another.
- Review security-sensitive findings and verify provider/network timeouts.
- Confirm main runtime/provider registry/UI all report the same implemented capabilities.
- For model adoption: license, supply chain, compatibility, evals and explicit human approval remain separate gates.

## Suggested next sequence

1. Merge/rebase the prerequisite provider and integration PRs only after tests and review.
2. Unify provider capabilities and route the production runtime through the shared provider contract.
3. Integrate Hugging Face discovery with persistence and change notifications.
4. Run candidate evals through actual provider adapters with enforced request deadlines.
5. Add coverage thresholds and optional-dependency smoke tests once a reliable baseline exists.
