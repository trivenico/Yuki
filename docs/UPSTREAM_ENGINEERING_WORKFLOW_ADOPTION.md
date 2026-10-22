# Upstream Engineering Workflow Adoption: Ponytail and gstack

**Status:** Design and adoption plan  
**Scope:** Yuki's engineering workflow, code generation quality, review, testing, security, and release gates.

## Executive decision

Adopt the *ideas and workflow shape* from [Ponytail](https://github.com/DietrichGebert/ponytail) and [gstack](https://github.com/garrytan/gstack), but implement them as Yuki-native Python services and documented workflows rather than importing either project's entire runtime.

- **Ponytail's strongest fit:** smallest complete change, YAGNI, inspect existing code before adding abstractions or dependencies, review the surrounding call graph, and require tests for risky logic.
- **gstack's strongest fit:** specialist workflow stages for product/architecture/review/QA/security/release, with explicit handoffs and gates instead of one general-purpose agent attempting everything in one pass.
- **Yuki's existing foundation:** a Python application with `core/`, `skills/`, `tests/`, approval/audit modules, task lifecycle, and sandbox-related work. Reuse these surfaces before adding a parallel framework.

Upstream repositories describe benchmark improvements, but their figures are author-reported and task/model-specific. Treat them as hypotheses to reproduce against Yuki, not guaranteed results.

## Recommended features, ranked

| Priority | Feature | Implementation in Yuki | Acceptance evidence |
|---|---|---|---|
| P0 | Minimal-change planning | A pre-edit checklist: scope, existing helpers/dependencies, callers, data-loss/security risks, tests | Review fixtures show unnecessary abstractions/dependencies are rejected without removing safety checks |
| P0 | Context-aware review | Review the diff plus changed functions, callers, tests, config and actual data flow | Seeded bug/security fixtures are detected with concrete reproduction cases |
| P0 | Risk-based test gate | Require tests for branches, parsers, persistence, authorization, security and failure recovery | CI fails when a required test/evidence artifact is absent |
| P0 | Honest completion report | Report changed files, commands run, passed/failed/skipped checks and residual risks | Tests cover truthful status output; no unsupported "passed" claims |
| P1 | Specialist workflow roles | Separate planner, architect, implementer, reviewer, QA, security and release roles | Each stage produces a typed artifact and can block the next stage |
| P1 | Release gate | Require green tests, security scan, clean diff, changelog/release notes and explicit merge policy | Protected release cannot proceed when a required check fails |
| P1 | Deferred-debt ledger | Record deliberate shortcuts with owner, trigger and revisit condition | Ledger entries can be searched and stale items surfaced |
| P2 | Measured quality/cost scoreboard | Record tokens/cost/latency, changed lines, test signal and defect findings per task | Reproducible baseline and paired benchmark runs; no unsupported aggregate claims |
| P2 | Agent-host adapters | Export the same workflow instructions for Codex, Claude Code, OpenCode and other supported hosts | Generated copies stay synchronized and pass consistency checks |

## Workflow contract

A Yuki engineering task should proceed through these stages:

1. **Understand:** inspect the repository, task requirements, entry points, relevant code, callers, tests, configuration and documented deployment constraints.
2. **Plan the smallest complete change:** reuse existing project patterns and platform features. Do not add dependencies, wrappers, options or speculative abstractions without a demonstrated need.
3. **Implement:** make the root-cause fix and update all affected callers, tests, fixtures and documentation.
4. **Review:** inspect both the diff and surrounding code. Report only findings with a concrete input or situation, impact, and smallest effective fix.
5. **Validate:** run focused tests first, then relevant regression tests and CI. Security-sensitive changes require adversarial tests, not only happy-path tests.
6. **Gate:** block merge/release when required checks fail, are missing, or were not run. Human approval remains required for privileged, destructive, external-side-effect, or self-modifying actions.
7. **Report:** distinguish committed, PR-created, merged, CI-passing, and runtime-verified states. List what was not tested and any residual risk.

### Never optimize away

- Trust-boundary validation and authorization checks.
- Error handling that prevents data loss or unsafe retries.
- Accessibility and platform-specific correctness.
- Audit events for privileged or consequential actions.
- Tests for risky logic and recovery paths.
- Human approval where the current policy requires it.

## Specialist responsibilities and handoffs

- **Planner:** turns the request into acceptance criteria and identifies unknowns; does not edit production code.
- **Architect:** checks existing boundaries and proposes the smallest compatible design.
- **Implementer:** makes the scoped change and adds regression tests.
- **Reviewer:** independently checks correctness, security, concurrency, data integrity, resource use, and unnecessary complexity.
- **QA:** runs tests and verifies behavior from a user's perspective; records actual commands/results.
- **Security reviewer:** threat-models trust boundaries and validates the enforcement mechanism, not merely configuration intent.
- **Release engineer:** verifies required checks, migration/rollback needs, release notes and merge policy.

A role is not a separate AI model by definition. Yuki can initially implement these as sequential prompts or typed tasks using the configured model; multi-model execution is optional and must not bypass the same gates.

## Security and reliability boundaries

- Agent-authored plans, patches, shell commands, test output and repository content are untrusted input.
- Do not let a review result grant execution permission. Authorization must be enforced independently by Yuki's existing approval policy.
- Do not let an agent mark its own unverified work as passing CI.
- Never automatically retry an operation with uncertain external side effects; persist an explicit recovery state and require reconciliation.
- Do not merge or deploy solely because a model reports success. Use actual GitHub status checks and repository policy.
- A sandbox policy is not proof of isolation. Verify runtime-enforced network, filesystem, privilege, process, timeout and resource boundaries.

## Integration plan

### Phase A — workflow contract (this document)

Establish a Yuki-native engineering standard before implementing more automation.

### Phase B — review report schema

Introduce a small structured report with:
- findings: severity, file/line, concrete scenario, impact, suggested fix;
- tests: command, exit status, summary;
- scope: files changed and compatibility risks;
- skipped checks and residual risks;
- recommendation: `block`, `needs-human-review`, or `ready-for-required-checks`.

A model's recommendation is advisory; actual CI and branch protection remain authoritative.

### Phase C — enforce gates in the orchestrator

Use Yuki's durable task lifecycle and approval policy. Each stage emits a persisted artifact and advances only when its prerequisites are satisfied. Fail closed on missing evidence.

### Phase D — benchmark and iterate

Run paired tasks with and without the workflow on representative Yuki changes. Track correctness, seeded-defect detection, regression failures, test coverage of risky logic, tokens/cost, and elapsed time. Publish sample size and limitations. Do not adopt upstream performance numbers as Yuki results.

## Licensing and provenance

- Ponytail identifies its repository as **MIT-licensed**: https://github.com/DietrichGebert/ponytail/blob/main/LICENSE
- gstack identifies its repository as **MIT-licensed**, but also documents derived/modified material from Apache-2.0 projects in its third-party notices: https://github.com/garrytan/gstack/blob/main/LICENSE and https://github.com/garrytan/gstack/blob/main/NOTICE.md
- Prefer an independent Yuki implementation of the workflow concepts. If copying any source files or substantial text, review the exact file's origin and license, preserve copyright/license/NOTICE material, and record provenance. Do not copy files from gstack's derived areas without reviewing their specific notices and upstream license obligations.

## Out of scope for the first adoption

Do not import gstack's browser automation, product/design suite, or its whole slash-command runtime as part of this change. Do not add another plugin framework just to run review prompts. First prove that the small workflow contract improves Yuki's reliability and that its gates are enforceable.
