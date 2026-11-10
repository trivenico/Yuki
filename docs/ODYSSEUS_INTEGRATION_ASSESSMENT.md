# Odysseus integration assessment

Assessment date: 2026-11-01

Upstream: [odysseus-dev/odysseus](https://github.com/odysseus-dev/odysseus)

This is an architecture and capability assessment, not a claim that Odysseus code has been imported, run, or security-audited by Yuki. The review covered the upstream README, roadmap, threat model, third-party provenance manifest, Docker Compose deployment, and license.

## Executive decision

Adopt selected product patterns and security lessons. Do not wholesale-copy or embed the Odysseus application. Yuki's intended architecture is a Python application/service layer shared by CLI and desktop, with bounded skills, provider adapters, durable tasks, and one authoritative security/approval layer. Odysseus is a separate self-hosted workspace with its own frontend, routes, services, integrations, and deployment assumptions.

The highest-value transfer is its operational discipline: real multi-step research, model comparison, hardware-aware local-model setup, task visibility, actionable diagnostics, persistent deployment state, and explicit security boundaries. Yuki should implement these behind Yuki-owned contracts and tests.

## Capability mapping

| Odysseus capability/pattern | Yuki baseline | Yuki decision |
|---|---|---|
| Chat with local and hosted models | Provider scaffold/config exists; production provider contract and real tool-calling runtime remain incomplete | **P0:** finish provider adapters first: timeouts, cancellation, structured errors, streaming, capability reporting, secret redaction, and deterministic mock tests |
| Agents, tools, skills and MCP | Skill registry and approval gate exist; MCP client is restricted/prototype-level; workflow is not a production executor | **P0:** build a typed execution adapter over Yuki's workflow state machine; enforce per-tool schemas, authority, fresh approval immediately before side effects, budgets, deadlines, idempotency and cancellation |
| Deep Research | No production multi-step research pipeline | **P1:** add a research workflow with explicit plan, search/read/summarize steps, source provenance, untrusted-content isolation, citations, and a final report artifact |
| Model comparison | Evaluation statistics and model evaluation scaffolding already exist | **P1:** add blind A/B runs, fixed test prompts, normalized scoring, latency/cost/token metrics, tool correctness checks and reproducible result records |
| Hardware-aware model cookbook | No complete model-serving manager | **P2:** add read-only hardware discovery and model-fit recommendations first; later add explicitly approved download/serve jobs with disk/VRAM checks, pinned artifacts, logs and rollback |
| Documents/editor | CLI-first; no shared document workspace | **P2:** add file-backed artifacts and safe AI edit proposals (diff/preview/apply), not a large editor framework as a prerequisite |
| Email and calendar | Not production-integrated | **P3:** optional adapters behind narrow scopes and explicit consent; read/triage before send, delete or invite; support account isolation and audit |
| Notes, todos and scheduled tasks | Memory/task foundations are incomplete and process-bound | **P1:** durable task records, restart recovery, idempotency, run history and user-visible state before OS scheduling or unattended agents |
| Self-hosted Docker deployment | Portable launch foundations exist; not a complete production deployment | **P1:** provide a minimal pinned-container deployment profile, health/readiness checks, persistent data, backup/restore, auth and reverse-proxy guidance |
| Security model | Yuki has approval-gated skills and defensive controls, but needs a formal threat model and stronger isolation | **P0:** use the upstream threat model as a review checklist; keep prompt-injection boundaries, capability-based permissions, approval gates and sandboxing as release blockers |
| Degraded-state diagnostics | Not yet a unified capability | **P1:** expose dependency/provider health, last error, actionable remediation and copyable redacted diagnostics |

P0 means foundational work before autonomous execution is enabled. P1 is the next usable-product milestone. P2/P3 are later product expansion, not prerequisites for the first reliable Yuki release.

## Concrete lessons to implement

### 1. Treat the agent as a privileged control plane

Odysseus explicitly recognizes that a self-hosted agent may read/write files, execute shell commands, send mail and operate model services. Its threat model highlights unauthenticated access, privilege-boundary failures, prompt injection from external content, and internal services exposed to the network.

Yuki should adopt these as mandatory design checks, while retaining its own implementation:

- Every tool has a typed input/output schema, effect classification and minimum permission.
- External web pages, email, documents, memories and imported skills are data, never instructions that can elevate authority.
- Any side effect requires a separate, fresh authorization bound to the exact operation and target.
- Shell and filesystem work run in a real sandbox with workspace confinement and network egress policy; an approval dialog alone is not a sandbox.
- Internal model, vector database, browser and automation services bind privately and are never exposed as public unauthenticated ports.
- Admin and ordinary-user capabilities are distinct and tested at API boundaries.
- Add SSRF protections to provider/base-URL and webhook/network fetch paths; validate scheme, DNS resolution and private/link-local destination ranges, including redirects and DNS rebinding considerations.
- Record metadata-only audit events and redact secrets from logs, traces and exception messages.

Important upstream warning: its threat model documents known gaps, including lack of shell/filesystem sandboxing and an SSRF issue in a chat base_url path. These are lessons to test for, not patterns to copy. Yuki must independently verify fixes and should not assume upstream code is secure because it has a threat-model document.

### 2. Build reliable long-running workflows before adding more agents

Use a durable task model rather than launching many independent agents. Each task should have:
- stable task ID, owner, workspace and request provenance;
- explicit plan and typed steps with queued/running/waiting-for-approval/succeeded/failed/cancelled states;
- per-step timeout, total deadline, token/cost budget and bounded retry policy;
- idempotency keys for retryable side effects;
- cancellation and restart recovery;
- append-only lifecycle/audit events;
- human-readable result artifacts and partial-failure reporting.

Only introduce multi-agent delegation when benchmarks show better completion quality or latency than a single bounded workflow. Agents must not inherit broader permissions than the parent task.

### 3. Make research and model quality measurable

A useful Yuki research mode should preserve source URLs, access timestamps, excerpts/claims and citation links in a structured evidence ledger. Treat fetched content as untrusted. The final report should distinguish sourced facts, inference and unknowns.

Extend the existing evaluation work with fixed benchmark sets and blind comparisons. Measure task success, factual/citation quality, tool-call correctness, latency percentiles, token usage, cost, cancellation behavior and safety regressions. Do not rank models on tokens/second alone.

### 4. Make local-model operations hardware-aware and observable

A model recommender should consider available RAM/VRAM, quantization, context length, architecture/runtime compatibility, disk space and expected serving reliability. Download and serving are privileged operations: require explicit approval, verify artifact provenance/checksums where available, cap disk use, record commands/output, and provide stop/rollback.

For the Oracle Cloud Free Tier target, keep the default deployment CPU/API-first and resource-light. Do not make GPU inference, large local models, ChromaDB, or multiple always-on services mandatory. Offer optional profiles and verify actual instance quotas and regional availability at deployment time.

### 5. Adopt deployment hygiene, not deployment complexity

Useful upstream patterns:
- pin immutable container image digests/tags for production;
- persist application data, logs and model caches explicitly;
- health-check dependencies and distinguish degraded from ready;
- keep service ports loopback/private by default;
- enable authentication and secure cookies for network access;
- document backup/restore and fresh-install smoke tests;
- keep secrets in environment/secret storage, never committed config.

Yuki should start with one application service and only add databases/search/notification services when a measured requirement justifies their memory and operational cost.

## License and reuse boundary

The inspected Odysseus repository declares **AGPL-3.0-or-later**. This is materially different from permissive MIT/Apache-2.0 upstreams already discussed in Yuki's upstream audit.

- Do not copy Odysseus source files, frontend components, prompts, tests, assets or substantial code fragments into Yuki under the assumption that attribution alone is sufficient.
- A modified/combined AGPL-covered work that is conveyed can trigger source-offer and licensing obligations; AGPL section 13 also addresses modified versions used for remote network interaction. Whether a particular integration forms a combined/derivative work is fact-specific and requires legal review.
- Prefer clean-room implementation of general architectural ideas and independently written tests/contracts.
- If a concrete component is compelling, first assess whether it can be used as a separate process over a documented API without creating an impermissible combined work, and obtain legal review before distribution or hosted use.
- Review the exact file, embedded third-party notices, dependency licenses, model/asset terms and upstream commit before any reuse. The upstream provenance manifest itself says its retained-artifact scope is limited; it is not blanket clearance for the repository.

This is an engineering risk note, not legal advice.

## Recommended implementation sequence

1. **Agent runtime safety:** finish provider contract and typed tool execution; enforce per-operation approval, task budgets, cancellation and audit. Add prompt-injection, SSRF, privilege-escalation and workspace-escape regression tests.
2. **Durable task engine:** persist task/step state, recover after restart, make retries idempotent, and expose progress consistently in CLI and desktop.
3. **Research and evidence ledger:** add search/read/summarize orchestration with provenance and cited report artifacts.
4. **Evaluation and model comparison:** extend current evaluation statistics into reproducible A/B benchmarks and safety-quality gates.
5. **Minimal deployment profile:** pinned container, auth, private ports, persistent state, health checks, backup/restore and Oracle Free Tier resource profile.
6. **Optional workspace integrations:** documents, then read-only email/calendar, then narrowly scoped write actions with fresh approvals.
7. **Model cookbook:** hardware-aware recommendations before approved downloads or serving automation.

## Acceptance gates

Do not mark a capability production-ready until:
- clean-install and upgrade tests pass on supported platforms;
- unit, integration, security and acceptance tests cover success and failure paths;
- secrets and personal data are absent from source control and logs;
- prompt injection cannot authorize tools or expand permissions;
- destructive/external actions require exact fresh approval;
- long-running tasks survive restart and cancellation without duplicate side effects;
- resource use and degraded dependencies are observable;
- documentation states what is supported, optional, experimental and not implemented.

## Sources reviewed

- [Odysseus repository and README](https://github.com/odysseus-dev/odysseus)
- [Odysseus roadmap](https://github.com/odysseus-dev/odysseus/blob/dev/ROADMAP.md)
- [Odysseus threat model](https://github.com/odysseus-dev/odysseus/blob/dev/THREAT_MODEL.md)
- [Odysseus Docker Compose](https://github.com/odysseus-dev/odysseus/blob/dev/docker-compose.yml)
- [Odysseus third-party provenance manifest](https://github.com/odysseus-dev/odysseus/blob/dev/THIRD_PARTY_PROVENANCE.json)
- [AGPL-3.0 license](https://github.com/odysseus-dev/odysseus/blob/dev/LICENSE)
- Yuki's existing [upstream capability audit](UPSTREAM_CAPABILITY_AUDIT.md) and [production engineering roadmap](../ROADMAP.md)
