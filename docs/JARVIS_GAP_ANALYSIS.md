# Yuki vs. the fictional JARVIS: engineering gap map

This document compares Yuki's intended direction with capabilities depicted in the Iron Man films. JARVIS is fictional; the comparison is a product/engineering target, not a claim that its behavior is achievable in full or that any implementation has movie-level reliability.

## Current engineering principle

Prioritize a unified, testable runtime over a large count of disconnected features. Every capability should have a defined interface, explicit permissions, observable outcomes, bounded resource use, and an end-to-end test.

## Capability gap map

| Capability | Fictional JARVIS target | Yuki foundation | Main gap / next work |
|---|---|---|---|
| Natural conversation | Contextual, persistent dialogue | Configurable provider and guarded text interfaces | Unified session state across CLI, voice, and API; provider timeout/cancellation |
| Goal planning | Multi-step planning and adaptation | ApprovedTaskAgent and manifest-backed planner | Consolidate legacy and manifest task paths; durable task execution and verification |
| Tool/computer control | Broad control of systems and devices | Registered skills and manifest dispatcher | Route every action through one policy boundary; OS-level worker isolation |
| Approval and trust | Contextual delegated authority | One-time action-bound tokens, persistent pending actions, approval coordinator | Authenticated identity, application wiring, hardened confirmation UX, revocation and audit monitoring |
| Memory | Rich recall of history and environment | Local JSON and vector-memory components | Validated retrieval, privacy controls, retention/deletion, end-to-end memory tests |
| Proactive work | Monitor events and act when needed | Reminder and evolution foundations | Durable scheduler, triggers, user-defined autonomy budgets, safe notification/action policies |
| Audio and vision | Continuous multimodal awareness | Partial STT/TTS/wakeword/speaker components | Full duplex voice loop, barge-in, model/device readiness, consent-based capture, multimodal evaluation |
| Reliability | Seamless recovery and coordination | Health checks, telemetry, durable job and approval primitives | Bounded external-call timeouts, idempotent retries, restart recovery, graceful shutdown, SLOs |
| Learning | Rapid adaptation | Local evolution proposals and review flow | Only approved changes, reproducible evals, rollback, signed/versioned skill artifacts |
| Security | Highly trusted system control | Output guard, permission gate, capability manifests | Isolation, threat-model tests, secrets controls, authenticated API, security review |

## Phased roadmap

### P0 — Unify the action path
- Connect CLI/API task entrypoints to the manifest planner and approval coordinator.
- Remove silent fallback to legacy tool paths for model-planned execution.
- Configure SQLite pending-action, token, and audit stores in a protected workspace.
- Derive actor/session from trusted identity; never from model-generated data.
- Add an end-to-end task test: goal -> proposed action -> exact preview -> approval -> execution -> audit record.

### P1 — Durable and isolated execution
- Execute third-party tools in OS-enforced isolation with filesystem/network/resource restrictions.
- Use durable jobs with cancellation, checkpoints, idempotency, retry budgets, and restart recovery.
- Add hard process timeouts and process-tree cleanup.
- Add prompt-injection, path traversal, SSRF, secret access, malicious import, and resource-exhaustion tests.

### P2 — Proactive assistant
- Add a persistent scheduler and event bus.
- Let users explicitly configure allowed triggers, tools, action budgets, quiet hours, and escalation rules.
- Start with notifications and reversible low-risk actions; require confirmation for consequential external actions.
- Record why each proactive action ran and provide pause/disable controls.

### P3 — Multimodal interaction
- Integrate voice input/output, interruption handling, optional wake word, and vision behind explicit privacy controls.
- Share task/session state across CLI, voice, and API.
- Evaluate latency, transcription quality, speaker-verification false accepts/rejects, and safe failure behavior.

### P4 — Production operations
- Define availability/latency/error SLOs and alert thresholds.
- Pin and scan dependencies, perform secret scanning, publish test artifacts, and use signed releases.
- Complete security review and disaster-recovery exercises.
- Release only after all mandatory acceptance gates pass.

## Acceptance criteria

Do not label Yuki production-ready until:
1. Every model-planned action goes through the same policy enforcement and audit path.
2. Consequential actions require authenticated, action-bound approval.
3. Untrusted plugins cannot access host resources outside explicit OS-enforced grants.
4. Tasks can be inspected, cancelled, recovered, and safely retried after restart.
5. End-to-end safety tests, dependency/secret scans, supported-platform smoke tests, and security review pass.
6. Users can disable proactive behavior, inspect audit history, revoke grants, and delete retained data.

Yuki can be Jarvis-inspired without pretending to have omniscience or unrestricted authority. The target is a capable personal operator that is proactive within user-defined boundaries and honest about uncertainty.
