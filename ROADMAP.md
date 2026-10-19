# Yuki production engineering roadmap

## Current release status
A checked item means code and tests exist; it does not imply production certification.

## Phase 0 — Baseline
- [x] Main branch and CLI/config implementation inspected.
- [ ] Record full-suite baseline from a clean checkout.

## Phase 1 — CLI robustness (in progress)
- [x] Handle empty `/skill` without an index error.
- [x] Isolate unexpected dispatch exceptions to the current command.
- [x] Report configuration startup errors without raw tracebacks.
- [x] Add CLI regression tests.
- [x] Fix two CI-confirmed integration failures: default device-scanner construction and provider validation order.
- [ ] Resolve the remaining missing approval-flow dependency and rerun the full suite on the exact PR head.

## Phase 2 — Observability
- [ ] Standard-library logging and rotating workspace log.
- [ ] Wire diagnostics into core agent, YukiAgent, and Ollama provider.
- [ ] Append-only approval audit records that avoid secrets and prompt bodies by default.

## Phase 3 — Chat loop
- [ ] Provider-backed chat history, persona, and local memory context.
- [ ] Ollama `/api/chat` while preserving task-agent single-shot generation.
- [ ] Never parse chat output as commands or execute tools from chat responses.

## Phase 4 — CI and CLI tests
- [ ] Require green full-suite CI on exact PR head.
- [ ] Add dispatch smoke tests for remaining routes.

## Phase 5 — Packaging and environment
- [ ] Add pyproject.toml, console entry point, version, and release notes.
- [ ] Correct cloud environment documentation and optional stdlib .env loading.

## Phase 6 — Reminders and runtime
- [ ] Bounded reminder scheduler and graceful shutdown.
- [ ] Review/integrate existing runtime PRs in dependency order only after CI passes.

## Phase 7 — Command registry
- [ ] Refactor dispatch without changing command syntax or approval semantics.
- [ ] Reuse command handlers from dashboard.

## Phase 8 — Memory quality
- [ ] Unicode-aware search and broader memory retrieval.
- [ ] Memory doctor and reminder purge.
- [ ] Validate skill risk/description; remove or integrate orphan task list.

## Phase 9 — Documentation truth pass
- [ ] Reconcile provider status, roadmap, README claims, and threat model.

## Release gate
Do not mark a phase complete until the full suite passes on its exact PR head, behavior changes have regression tests, and fail-closed approvals and local-first routing have been reviewed.
