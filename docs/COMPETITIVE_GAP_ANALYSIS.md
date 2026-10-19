# Yuki competitive and integration gap analysis

Audit date: 2026-10-19. This is a public-feature and repository-architecture comparison, not a hands-on benchmark or security certification. Upstream features vary by release. A feature is not considered integrated into Yuki unless its implementation, configuration, tests, and operating path prove it.

## Executive decision

Yuki should be the user-facing companion and policy/control plane. Do not wholesale merge multiple agent frameworks into one process. Reuse standards and integrate selected upstream systems behind narrow adapters. Keep one identity/memory policy, one permission gate, one audit trail, and one capability registry.

## Comparison

| Project | Public differentiators | Gap to close in Yuki | Recommendation |
|---|---|---|---|
| [Hermes Agent](https://github.com/NousResearch/hermes-agent) | Persistent/procedural memory, model/provider choice, MCP, scheduled tasks, messaging gateways, subagents and terminal backends | Session search, durable scheduling, channel gateway, subagent isolation, interruptible long tasks | Reuse skill/provider/MCP patterns; consider an isolated optional worker after sandbox/data policy is defined |
| [OpenClaw](https://github.com/openclaw/openclaw) | Gateway-centric chat channels, cross-device companion nodes, extensions/skills and provider configuration | Unified gateway, account pairing/revocation, mobile/desktop nodes and channel operations | Integrate channel adapters narrowly; don't give a third-party gateway broad host permissions |
| [OpenJarvis](https://github.com/ open-jarvis/OpenJarvis) | Local-first personal AI, inference engine choices, hardware-aware runtime, deep research, scheduled agents and efficiency evaluation | Hardware-aware model routing, research evaluation, latency/cost/energy measurements | Benchmark as an optional local inference/research backend; retain Yuki's policy layer |
| [Letta](https://github.com/letta-ai/letta) | Stateful agent identity, cross-session memory, memory editing and consolidation | Memory hierarchy, search, reviewed consolidation, backup/portability | Study memory patterns; preserve user-approved memory and add versioned export/restore |
| [Open Interpreter](https://github.com/openinterpreter/open-interpreter) | Computer/code execution workflows and sandbox/approval patterns | OS-enforced isolation, network boundaries and protected paths | Adopt isolation patterns; never expose unrestricted shell/computer control to a model |
| [MCP](https://modelcontextprotocol.io/) | Standard tool/resource integration and server discovery | Runtime discovery, server identity/scopes, schema validation, timeouts, revocation and audit | Make MCP the preferred tool integration contract, behind central policy |
| [A2A](https://a2a-protocol.org/latest/) | Agent discovery, task handoff, status and result exchange | Agent cards, trust, scoped delegation, cancellation and remote-agent audit | Add after local identity, authorization and task lifecycle are solid |

Descriptions summarize public project documentation, not independently benchmarked performance. Fix any version-specific claims by re-checking official docs before adoption.

## Current Yuki evidence inspected

On the default branch:

- `core/agent.py` has a bounded registered-skill execution path and requests fresh approval for each invocation.
- `core/task_agent.py` has a bounded model-guided loop over registered skills; malformed JSON is rejected and arbitrary shell execution is not exposed through that loop.
- `core/permissions.py` fails closed if no approver is configured.
- `core/plugin_loader.py` warns that importing Python runs code with process privileges; plugin discovery is disabled by default.
- `providers/factory.py` supports mock, local Ollama, and opt-in hybrid routing. The default config uses mock.
- `companion/memory.py` provides bounded local file-backed memory, but this alone does not prove backup/restore, encryption, cross-process locking, or retention behavior.
- `.github/workflows/tests.yml` installs the single declared dependency and runs unittest discovery. It does not evidence type checking, linting, coverage thresholds, dependency vulnerability checks, integration tests, or a dedicated security job.
- `README.md` explicitly states Yuki is not production-ready. This audit agrees.

## Critical gaps

### P0 — before production

1. Reproducible release gates: supported OS/Python test matrix, compile check, lint/type checks, coverage policy, dependency/license and vulnerability scanning.
2. Runtime capability truth: distinguish configured, available, degraded, disabled and unverified. A module/config flag is not a health check.
3. Central authorization: principal, capability, resource scope, approval expiry, replay prevention and correlated audit events. Model output never grants permissions.
4. OS-enforced isolation for risky skills; fail closed when sandboxing is unavailable.
5. Secret/privacy lifecycle: OS secret storage, redacted logs, data classification for cloud calls, memory export/deletion/retention and tested backups.
6. Reliability: timeouts, retry/backoff, cancellation, idempotency, durable job state, restart recovery, health checks, metrics and alerts.
7. Threat model: prompt-injection, SSRF, secret leakage, path traversal, plugin trust, dependency review, incident response and rollback.

### P1 — high value next

- Provider capability negotiation and local-first routing with latency/cost telemetry and tested fallback.
- Search-provider adapter with citations/provenance, stale-source handling, source trust tiers and SSRF-resistant retrieval.
- MCP integration with per-server allowlists, scoped auth, tool filtering, timeouts, disconnect/revocation and integration tests.
- Durable task queue with idempotent scheduling, leases, retries, cancellation and restart recovery.
- One messaging channel with pairing, least-privilege scopes, outbound rate limits and revocation.
- Memory retrieval/search and versioned snapshots; consolidation should propose changes for review, not silently rewrite user history.
- Evaluation harness for task success, policy violations, prompt injection, latency, token cost and regression.

### P2 — optional

- A2A agent federation and specialist delegation.
- VAD, wake-word false-accept/reject measurements, visible microphone state and barge-in.
- Cross-device nodes, camera/screen workflows, smart-home and car control behind separate opt-in capabilities.
- Self-improving skills with staged changes, reproducible evaluations, review, signed releases and rollback.

## Target architecture

- **Yuki Core:** intent/task lifecycle, identity, consent, policy, capability registry and audit correlation.
- **Model router:** provider-neutral contract; local-first default; cloud opt-in by task/data classification.
- **Tool plane:** skills and MCP adapters; each tool declares schema, required capability, risk, timeout, idempotency and side effects.
- **Agent plane:** optional Hermes/Letta/OpenJarvis workers behind narrow adapters; workers return proposals/results, never permission grants.
- **Job plane:** durable queue, scheduler, cancellation, retry/backoff, idempotency keys, status and recovery.
- **Memory plane:** user profile, episodic/session history, semantic/project knowledge and procedural skills kept distinct; each needs retention/export.
- **Interface plane:** CLI/local UI first; add one messaging/voice channel at a time.
- **Operations plane:** health/readiness, metrics, redacted logs, traces, model/dependency inventory, backups, restore drills and rollback.

## Integration sequence

1. CI and truthful readiness reporting.
2. Central authorization/audit and isolated skill execution.
3. Provider health checks and real local-model smoke test.
4. Citation-based retrieval with adversarial fixtures.
5. Durable jobs and restart recovery.
6. MCP adapter and one end-to-end server.
7. One messaging channel.
8. Benchmark OpenJarvis as optional local inference/research engine.
9. Evaluate Hermes/Letta as isolated workers; avoid competing memory authorities.
10. A2A and device/voice control after authorization and observability gates pass.

## Definition of done

- CI green on supported environments; no skipped critical security tests.
- No critical/high unresolved findings; threat model and dependency inventory reviewed.
- External/destructive actions require scoped fresh approval and auditable events.
- Untrusted content cannot change tool permissions or policy.
- Sandbox and SSRF defenses pass negative tests.
- Memory export/restore/deletion and scheduled-task recovery pass drills.
- Readiness report matches runtime state.
- Logs redact secrets; monitoring and rollback are exercised.
- Human acceptance tests cover each enabled interface.
- Limitations are documented; no capability is called production-ready merely because a source file exists.
