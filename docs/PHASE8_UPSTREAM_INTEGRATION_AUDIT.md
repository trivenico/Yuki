# Production Engineering Phase 8 — Upstream Integration and Gap Audit

Audit date: 2026-10-21  
Repository baseline: `trivenico/Yuki`, default branch `main`, commit `30c3f83c00bac7514f1ace95fca9bd931c852956`.

## Executive decision

**Do not fork-and-copy entire agent codebases into Yuki.** Keep Yuki's existing Python architecture and port individual patterns only after inspecting the exact source file, license, dependency tree, threat boundary, and test behavior. A fork is appropriate only if Yuki intentionally wants to maintain a separate upstream distribution; it is not a safe shortcut for merging several agent runtimes.

The repository README already correctly labels Yuki as not production-ready. This audit does not change that status.

## What was verified in GitHub

- The repository is public and the connected GitHub identity has push/admin permissions.
- `main` currently points to `30c3f83c00bac7514f1ace95fca9bd931c852956`; its latest commit message is `revert: remove accidentally committed task tests from default branch`.
- That revert removed `tests/test_task_store.py` (121 lines). Current `main` does not expose `core/task_store.py` or `core/task_runtime.py` at those paths, so restoring that test file alone would create a broken test suite. Reintroduce the implementation and tests together only after deciding whether durable task execution belongs in the next phase.
- CI currently installs `requirements.txt` and runs `python -m unittest discover -s tests -v` on Ubuntu with Python 3.11. The checked-in workflow does not itself establish Windows/macOS compatibility, type checking, linting, dependency vulnerability scanning, or security-boundary coverage.
- `requirements.txt` currently declares PyYAML with a compatible version range rather than a fully locked dependency set.
- `README.md` and `SECURITY.md` explicitly disclose that the agent loop is bounded/prototype-grade, the model-guided task path is not a production-grade native tool-calling runtime, there is no production-grade sandbox, and a security review has not been completed. Preserve those disclosures until verified evidence supports changing them.

## Upstream projects and what to learn

| Project | Useful patterns to evaluate | Integration boundary |
|---|---|---|
| [Hermes Agent](https://github.com/NousResearch/hermes-agent) | Progressive skill disclosure, provider/toolset abstractions, persistent procedural skills, profile isolation, interruption and scheduling | Adopt skill manifest conventions and provider/tool contracts before porting a whole runtime. Review exact file-level license and dependencies. |
| [OpenClaw](https://github.com/openclaw/openclaw) | Explicit sandbox modes, separation of gateway/control plane from tool execution, security audit workflow, channel/device integration patterns | Treat its own docs as a design reference, not proof that defaults are hardened; sandboxing is documented as off by default. Keep risky execution isolated and policy-enforced. |
| [OpenJarvis](https://github.com/stanford-oval/OpenJarvis) | Local-first personal AI, device/runtime evaluation, task/agent abstraction and deployment profiles | Verify the canonical upstream repository and current license before importing code; the prior Yuki audit references Apache-2.0, but file-level provenance still needs review. |
| [AutoClaw](https://github.com/tsingliuwin/autoclaw) | Portable skill packaging, batch workflow concepts, OpenAI-compatible endpoints | Do not inherit unattended shell execution defaults. Require per-action authorization, deadlines, budgets, and a disposable sandbox. |

Official references reviewed for this audit include the [Hermes repository](https://github.com/NousResearch/hermes-agent), [Hermes skills documentation](https://github.com/NousResearch/hermes-agent/blob/main/website/docs/user-guide/features/skills.md), [OpenClaw sandboxing documentation](https://github.com/openclaw/openclaw/blob/main/docs/gateway/sandboxing.md), and [OpenClaw security policy](https://github.com/openclaw/openclaw/blob/main/SECURITY.md).

## Prioritized phase gaps

### P0 — Reproducible baseline and CI integrity

1. Run the current test workflow against the exact commit and retain the result; a workflow file alone is not evidence of passing tests.
2. Add a clean-checkout install/test check and a minimal dependency lock strategy. Keep optional hardware/model packages optional.
3. Add CI jobs for supported operating systems only after their compatibility claims are tested; start with import/path/config smoke tests on Windows and macOS.
4. Add lint/type checks incrementally and make failures actionable rather than adding many tools at once.
5. Confirm the reverted task-test change did not leave roadmap claims ahead of the code. Never restore tests for absent implementation files.

### P1 — Agent execution safety (highest product risk)

1. Define typed tool schemas and a single policy-enforcement point outside model prompts.
2. Re-authorize immediately before each side effect; approvals must be scoped to one action and must not silently carry over to retries or changed arguments.
3. Add deadlines, cancellation, maximum steps, output limits, retry budgets, idempotency keys, and partial-failure recovery.
4. Run file/shell/browser work in a least-privilege sandbox with explicit workspace mounts and network policy. Fail closed when the sandbox is unavailable.
5. Treat retrieved web pages, documents, skill files, model output, and tool output as untrusted data. Add prompt-injection and path-traversal regression tests.
6. Store audit metadata without recording secrets or unnecessary personal content.

### P2 — Provider and task lifecycle contracts

1. Define provider interfaces for timeout, cancellation, structured errors, token/cost metadata, and capability reporting.
2. Keep mock/offline tests deterministic; test provider failures, malformed output, timeout, rate limiting, and credential redaction.
3. Implement durable task persistence only with explicit state transitions, schema versioning, restart recovery, and tests. Do not imply a persistent task runtime exists solely because a planner or workflow prototype exists.

### P3 — Portability and operator experience

1. Detect OS, Python, available accelerators, local model servers, and optional dependencies without auto-installing or silently changing system settings.
2. Produce a capability/readiness report that distinguishes detected, configured, tested, and unavailable features.
3. Add backup/restore, memory export/deletion/retention, and migration tests before long-lived use.
4. Add messaging, mobile, browser, and device integrations one at a time with pairing, scoped permissions, rate limits, revocation, and integration tests.

### P4 — Evaluation and release gates

1. Build deterministic benchmark tasks for coding, companion workflows, and defensive file analysis.
2. Track success rate, latency, resource use, cost, policy violations, and recovery behavior.
3. Require tests, threat-model review, dependency review, and operator-facing release notes for each claimed production capability.

## Import and licensing controls

- Do not copy a repository wholesale to combine features.
- Preserve license and copyright notices for adapted/copied files; include required attribution and change notices.
- Review per-file headers, assets, skill content, model weights, and transitive dependencies separately. A repository-level license does not automatically cover every artifact.
- Prefer small adapters and independently tested implementations that preserve Yuki's existing permission gate.
- Record upstream URL, commit SHA, source paths, license, local changes, dependencies, and tests for every imported component.

## Exit criteria for this phase

- [ ] Current default-branch tests executed and results recorded.
- [ ] No test references a missing implementation module.
- [ ] CI has a reproducible dependency/install strategy.
- [ ] A single-action approval and sandbox failure policy is tested.
- [ ] Upstream code is imported only with traceable provenance and license review.
- [ ] README capability claims match tests and verified runtime behavior.

**Status:** audit and plan only. This document does not claim that runtime security gaps have been fixed or that tests have been executed.
