# Local-First Intelligence and Safe Evolution Contract

## Product objective

Yuki is a persistent, user-directed AI companion: a local-first personal intelligence system that can understand requests, retain user-approved context, plan work, coordinate specialist agents, operate approved tools, observe an explicitly enabled desktop session, and improve its own software through a controlled engineering process.

The Iron Man JARVIS analogy is a product direction, not a claim that current AI has human-like consciousness, unlimited reasoning, or infallible autonomy. "Quantum-level" describes the ambition for exceptional engineering and capability; it does not imply quantum hardware or quantum advantage.

## Non-negotiable architecture decisions

1. **Local-first, not local-only.** Core orchestration, identity, policy, task state, memory, audit, and recovery must work without an external model API. A supported local model is the default intelligence backend when configured. Cloud models are optional accelerators selected by explicit policy and consent.
2. **Provider-independent brain.** Domain logic depends on capability interfaces, not vendor SDKs or provider-specific request formats. Providers are replaceable adapters. No cloud key, provider account, Oracle Cloud account, or network connection is required to boot the local core.
3. **Graceful capability degradation.** If no model is available, deterministic functions (task status, local search, reminders, policy checks, queued work, recovery, and approved local workflows) remain available. Model-dependent features report unavailable rather than pretending to reason.
4. **Persistent but user-governed identity and memory.** Memory records have provenance, source, timestamp, sensitivity, retention policy, and user-controlled correction/deletion. Inference is not silently promoted to fact. Sensitive memory is encrypted at rest where supported.
5. **Specialists are workers, not authorities.** Agent manifests describe capabilities. The runtime independently enforces identity, tool allowlists, data scopes, budgets, approvals, and deadlines. Agent outputs are untrusted until validated.
6. **Self-improvement is a controlled release pipeline.** Yuki may detect failures, propose changes, prepare isolated patches, run tests, scan dependencies, evaluate regressions, and produce a review package. It may not silently rewrite its own trusted runtime, security policy, credentials, identity, or approval rules.
7. **Verifiable actions.** A task is complete only after its declared postconditions are checked. Every state transition, approval, tool invocation, provider route, upgrade, and rollback is auditable.
8. **Portable by design.** User data, models, logs, caches, and source/runtime locations are configurable independently. Removing an external drive must not corrupt committed state; writes should be checkpointed and shutdown/eject should be explicit.

## Logical architecture

```text
User interfaces: voice | chat | desktop | mobile/web | API
                         |
                Companion / Session Core
                         |
       Identity + Context + Memory + User Preferences
                         |
       Intent + Reasoning + Planning + Verification
                         |
            Policy / Consent / Approval Gate
                         |
           Durable Task Runtime + Event Journal
                         |
             Specialist Agent Coordinator
                         |
       Capability Interfaces / Tool Broker / IPC
                         |
 Local providers | Optional cloud providers | Local tools
                         |
 SQLite/local encrypted stores | Optional Oracle sync
```

### Brain and provider boundary

Define stable internal contracts for at least:
- `TextGeneration`: bounded prompt and structured response.
- `StructuredGeneration`: schema-constrained output with validation.
- `Embeddings`: optional semantic indexing; lexical search remains a fallback.
- `Vision`: image/frame understanding behind explicit capture and disclosure.
- `SpeechToText` and `TextToSpeech`: local implementations preferred where feasible.
- `ToolCalling`: proposals only; execution always goes through the policy/tool broker.
- `ModelCapabilities`: context limits, modalities, structured-output support, tool support, local/cloud classification, and measured health.

A provider adapter must declare capabilities and data-handling properties. Routing chooses only among adapters explicitly enabled for the task and data classification. It must not silently send private content to a cloud provider. If no permitted adapter can satisfy a request, explain the limitation or use a deterministic fallback.

### Memory system

Separate memory into:
- Working context: short-lived session/task state.
- Episodic memory: user-approved summaries of completed interactions and outcomes.
- Semantic memory: validated facts with provenance and confidence.
- Procedural memory: reviewed workflows and preferences.
- System memory: versioned configuration and operational state, never mixed with personal recollections.

Memory writes are typed, bounded, attributable, and reversible. Retrieval must enforce owner, sensitivity, and workspace scopes. A model-generated statement is not automatically a trusted memory.

### Safe evolution and self-upgrade

The evolution pipeline is:

1. **Observe:** collect explicit user feedback, task outcomes, test failures, and bounded operational metrics. Do not silently ingest unrelated files, screen contents, or private conversations.
2. **Diagnose:** correlate evidence and identify likely causes; label uncertainty and distinguish observations from hypotheses.
3. **Propose:** create an immutable change proposal with rationale, affected components, expected behavior, risk, data impact, rollback plan, and acceptance tests.
4. **Isolate:** create a separate branch/worktree or disposable build environment. Never edit the running trusted installation in place.
5. **Build:** generate a patch limited to approved paths and capabilities. Changes to policy, identity, credentials, approval logic, or audit controls require explicit human-led review and cannot be self-approved.
6. **Verify:** run unit, integration, security, dependency, packaging, migration, and cross-platform tests; compare behavior against a known baseline; reject missing or inconclusive evidence.
7. **Review:** show the exact diff, test evidence, risk assessment, and release notes. Human approval is required before installing an upgrade into the trusted runtime.
8. **Stage and health-check:** install to a versioned inactive slot, verify integrity/signature and health, then switch atomically where the platform supports it.
9. **Rollback:** automatically revert a failed health check to the last known-good version; preserve logs and never discard user data.
10. **Learn:** record the verified outcome and user decision as evidence for future proposals, not as an unconditional rule.

The existing `companion/evolution.py` is an observation-to-proposal foundation only. It does not implement these upgrade stages and must not be described as autonomous self-upgrading.

## Autonomy levels

- **Level 0 — Explain:** answer and summarize; no state changes.
- **Level 1 — Prepare:** draft plans, commands, messages, and patches without applying them.
- **Level 2 — Approved execution:** execute a narrowly scoped action after fresh approval.
- **Level 3 — Bounded delegation:** run pre-approved, reversible workflows within explicit budgets, scopes, and expiration.
- **Level 4 — Supervised maintenance:** prepare and test software upgrades; human approval required to activate them.
- **Never implicit:** unrestricted shell, silent credential access, privilege escalation, policy self-editing, unbounded network access, or autonomous destructive actions.

Autonomy is a policy decision enforced outside the model. A model cannot grant itself a higher level.

## Oracle Cloud and other remote services

Oracle Cloud is an optional remote compute/storage/synchronization target. The local system remains usable without it. Cloud sync must be encrypted, scoped, resumable, conflict-aware, and explicitly enabled. Use short-lived credentials and owner-bound execution contexts; never store cloud secrets in the portable repository or plaintext config. A remote service outage must not invalidate local memory or task history.

## Portable desktop and screen control

The desktop shell is a separate interface to the same authenticated local core. Screen capture and input control are distinct capabilities with separate permissions. Screen observation is visible, session-scoped, and off by default. Actions must be previewed where practical, bounded to the active user session, logged, and interruptible. The portable drive must not bypass host OS security, endpoint protection, or user permissions.

## Performance and resilience targets

Targets must be measured on declared reference hardware before being advertised:
- Core startup and health checks do not require network access.
- Local data remains readable and recoverable after an interrupted shutdown.
- Tasks have durable IDs, bounded retries, idempotency keys, deadlines, cancellation semantics, and explicit recovery states.
- Every worker has resource/time budgets and a health signal.
- Provider timeouts and outages do not deadlock the task runtime.
- Portable storage operations tolerate paths with spaces and removable-drive loss.
- Backups are encrypted, versioned, and restore-tested.

## Delivery sequence

1. Stabilize the current provider/profile/security PR stack and fix cross-platform quality-gate failures.
2. Define and test provider capability contracts; verify true no-cloud startup and local-model routing.
3. Consolidate memory provenance, privacy scopes, encryption, export, and deletion behavior.
4. Connect the durable task runtime, specialist coordinator, worker pool, verification, and audit into one end-to-end flow.
5. Build the supervised evolution proposal and isolated patch pipeline.
6. Add versioned staged upgrades, integrity verification, health checks, and rollback.
7. Complete portable runtime packaging and Windows/macOS/Linux hardware acceptance.
8. Integrate Oracle Cloud as an optional encrypted sync/compute adapter.
9. Add supervised desktop observation/control and multimodal companion experiences.
10. Expand the 100 specialist roles only as each role has a tested handler, capability contract, least-privilege policy, evaluation set, and operational owner.

## Definition of done

A capability is not complete because a module, agent name, mock, or design document exists. It is complete only when:
- it is reachable through the intended product interface;
- it has a real implementation and documented dependencies;
- authorization and data boundaries are enforced by runtime code;
- success and failure outcomes are observable and recoverable;
- tests cover normal, adversarial, offline, and cross-platform behavior as relevant;
- limitations are explicit and verified;
- installation and user documentation match actual behavior.
