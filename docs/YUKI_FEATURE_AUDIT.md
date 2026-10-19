# Yuki requested feature audit and implementation roadmap

## Executive summary
The supplied list is a product vision, not a verified inventory of working features. The repository README explicitly says Yuki is not production-ready and describes several modules as prototypes or disconnected primitives. A filename or registered skill does not prove an end-to-end capability.

This phase adds a metadata-only office catalog for 32 role archetypes and enterprise roles. It does not start 32 independent threads, impersonate the named public figures, call 32 LLMs, or grant roles tool permissions.

## Capability audit

| Area | Current evidence/status | Next work |
|---|---|---|
| Terminal runtime | main.py exists; optional capabilities need dependencies | Fresh VM install and startup/shutdown smoke tests |
| Local model | Ollama provider exists | Model availability, timeout, memory and CPU limits |
| Hybrid model | Local-first HybridProvider exists with named task allow-list | Test confirmation at every cloud-send call site and no silent fallback |
| Skills | Static registry and opt-in plugin loader exist | Skill contract tests, permissions and dependency health |
| Prompt injection | Heuristic scanner exists | Treat as a signal, not a security boundary; isolate tools |
| Memory | Local companion storage, lexical search and knowledge graph components exist | Retention, export, deletion, backup/restore and encryption |
| Voice | Optional STT/TTS/wakeword/speaker modules exist | Hardware tests, visible mic state and retention controls |
| Virtual office | Role skill/planner and this metadata catalog | Durable queue, real bounded workers, budgets and cancellation |
| Hot reload | Plugin discovery prototype; importing Python executes code | Keep disabled; trust policy and isolated worker process |
| API server | Production-grade API not established in README | Auth, authorization, rate limits, audit and deployment hardening |
| 3D office | Live dashboard not established as implemented | Build only after a real worker-state/event API exists |
| Earning companion | Monetization/content/SEO skills are registered | Cited research, opportunity scoring, effort/cost/risk and outcomes |
| Self-evolution | Evolution module exists; safe end-to-end auto-modification unverified | Proposal-only diffs, isolated tests, human review and rollback |
| Cybersecurity | Defensive foundations exist; high-risk list items are not enabled by default | Authorized targets, sandboxing and audit; no hack-back |

## Architecture decisions
1. Local-first routing; cloud calls require a named allow-listed task, explicit user approval and data minimization. No silent cloud fallback.
2. Role profiles structure plans and reports but never grant permissions. Existing permission gates and per-tool policies remain authoritative.
3. Sending, publishing, purchasing, deleting, code execution, external scanning and device control require explicit approval.
4. Untrusted plugins are code execution. Keep auto-discovery disabled by default; a prompt scanner is not a sandbox.
5. Report configured profiles, queued tasks and live workers separately. Never report a profile as a running agent unless a worker actually exists.
6. Do not store hidden chain-of-thought. Store concise rationales, plans, evidence, uncertainty and verification results.
7. Do not claim OCR accuracy or tokens/second without reproducible benchmarks on actual target hardware.

## Prioritized roadmap

### P0 — trustworthy single-user MVP
- Resolve existing CI failures and get a green PR workflow.
- Fresh cloud VM install and mock-mode smoke test.
- Provider timeouts, secret scanning, dependency review and log redaction.
- Approval-flow integration tests and fail-closed behavior.
- Backup/restore and workspace-boundary tests.

### P1 — daily personal and earning companion
- Local chat and task history with export/delete.
- Research briefs with source URLs, timestamps and uncertainty.
- Opportunity tracker with evidence, effort, costs, risks, next action and actual outcomes.
- Draft-first content and communication workflows; no autonomous publishing.
- Opt-in daily briefing and reminders.

### P2 — real multi-agent office
- Persistent SQLite job queue and event log.
- Bounded worker pool (start with 3–5, not 32 threads by default).
- Typed task envelopes, deadlines, cancellation, retry limits and per-task budgets.
- Role briefs, independent QA reviewer and artifact provenance.
- Distinguish profiles, queued jobs, active workers, completed jobs and failures.

### P3 — voice and multimodal
- Push-to-talk first; wake word only with consent and visible microphone state.
- No ambient screen/camera capture by default; opt-in and configurable retention.
- Verify model downloads and offline STT/TTS smoke tests.

### P4 — controlled self-improvement
- Draft patches on separate branches.
- Run tests, security checks and integration tests in CI.
- Require human review and explicit merge approval.
- Agents may not approve or merge their own changes.

## Definition of done
A feature is implemented only when wired into runtime, tested, documented with dependencies/permissions, and passing CI. Production readiness additionally requires deployment smoke tests, monitoring, backup/restore, privacy lifecycle, security review and tested rollback.