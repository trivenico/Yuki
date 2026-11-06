# Architecture Research: Agent Ecosystem and AI Credits

**Status:** Research accepted; implementation deliberately staged  
**Date:** 2026-11-06  
**Scope:** ruvnet open-source ecosystem and Find AI Credits

## Executive decision

Yuki should adopt the architectural patterns, not wholesale framework dependencies. The existing Python task engine, approval controls, provider adapters, companion services, CLI, and Tauri desktop app remain the system of record. New capabilities must integrate through those contracts rather than create a parallel runtime.

No upstream code or runtime dependency is copied by this research record. Before any code reuse, pin an exact commit, inspect the complete dependency tree, confirm the applicable license and notices, run a security review, and add a reproducible integration test.

## 1. ruvnet ecosystem: findings and fit

Repositories inspected: Ruflo, RuVector, MetaHarness, RVM, AgentDB, and APx. The upstream READMEs describe complementary areas:

| Project | Useful pattern | Yuki fit | Decision |
|---|---|---|---|
| Ruflo | Specialist-agent coordination, workflow routing, MCP, shared context | Existing specialist coordinator and durable task runtime | Learn orchestration patterns; do not add a second scheduler |
| RuVector | Semantic retrieval, graph relations, feedback-informed ranking | Existing memory and context assembly | Prototype behind a memory adapter after retrieval benchmarks |
| AgentDB | Episodic memory, skill records, feedback loop, portable memory container | Memory lineage and retrieval quality | Reuse concepts; preserve Yuki-owned schemas and export format |
| MetaHarness | Repo-aware harnesses, explicit policies, evaluation and provenance | Engineering agent, skill governance, change review | Adopt evaluation gates and evidence records, not automatic self-modification |
| RVM | Capability-bounded execution, isolation, checkpointing, witness records | Tool execution and worker isolation | Treat as a longer-term sandbox research track; not a drop-in replacement for OS isolation |
| APx | Accepted-work measurement with human baseline, elapsed time, cost and supervision | Agent quality and productivity telemetry | Adopt task-level measurement contract; never present synthetic tutorial scores as real productivity |

### Architectural principles to adopt

1. **One control plane:** specialist agents propose typed tasks; the existing durable task engine owns idempotency, dependencies, leases, retries, cancellation and recovery.
2. **Memory with provenance:** store source, owner, timestamp, confidence, retention class, model/embedding version and retrieval feedback. A similarity score is not proof of truth.
3. **Evidence-gated learning:** candidate prompts, skills, routing policies or memory-ranking changes run in a sandbox against a fixed evaluation set. Promotion requires regression checks, reviewer/owner approval and a reversible version.
4. **Capability isolation:** tool permissions are enforced outside model prompts. MCP transport or a portable memory package must never confer execution authority.
5. **Measure useful outcomes:** record accepted task result, elapsed wall time, provider/model, token usage where available, estimated cost, retries and human review. Keep measured data distinct from estimates and synthetic examples.

## 2. Find AI Credits: findings and fit

Find AI Credits is a curated discovery directory for provider credits, trials, discounts and startup programs. Its useful product patterns are category filters, eligibility conditions, direct official-provider claim links, verification dates, expiry information and step-by-step claim guidance.

It is **not** an API billing ledger, provider entitlement API, or guarantee that Yuki qualifies for a listed offer. Listings and monetary headline values require rechecking against official provider terms before action.

### Proposed Yuki capability: AI Infrastructure & Credits Agent

- Discover public credit, grant, startup, student and open-source programs from configured sources.
- Normalize each offer into a versioned record: provider, official claim URL, offer type, amount/currency, eligibility, geography, stage, prerequisites, expiry, last-verified timestamp, source evidence and confidence.
- Match offers against an explicitly supplied Yuki/project profile; identify missing facts instead of guessing eligibility.
- Generate a preparation checklist and draft application answers from user-approved project facts.
- Track provider API spend separately from promotional-credit opportunities.
- Alert before known offer deadlines or configured spend thresholds.
- Require explicit approval before account creation, application submission, payment, billing changes, or disclosure of personal/company data.

### Cost and credit accounting

Keep these concepts separate:

- **Usage ledger:** provider/model, request ID, timestamp, input/output tokens, cached tokens if exposed, currency, pricing catalog version and estimated/settled cost.
- **Budget policy:** per-provider, per-model, per-task and per-period limits; hard-stop behavior must be enforced before dispatch when possible.
- **Credit opportunity catalog:** external programs and their eligibility/expiry metadata. Do not count an unapproved or unredeemed offer as available balance.
- **Actual balance:** only read from an authenticated provider billing API or explicitly entered and confirmed by the user. Never infer it from a directory listing.

Use decimal arithmetic for money, retain the original provider usage response, version price tables, and label estimates clearly. Do not route traffic to a provider merely because it advertises credits: evaluate capability, privacy, reliability, latency, data residency and user policy first.

## 3. License and supply-chain notes

The inspected upstream LICENSE files identify Ruflo, RuVector, MetaHarness and AgentDB as MIT-licensed. RVM's repository README advertises MIT OR Apache-2.0; confirm the exact file and crate-level notices before reuse. APx's root LICENSE was not confirmed during this review.

MIT licensing does not remove the need to preserve copyright and license notices. Dependencies, model weights, datasets, generated assets and third-party integrations can have separate terms. No source files are copied by this research record.

## 4. Implementation sequence

### P0 — Contracts and visibility
- Extend provider usage records only where actual adapter responses expose reliable usage.
- Introduce a versioned price catalog and decimal cost calculation with explicit unknown-price behavior.
- Add cost/usage visibility to CLI and desktop without changing provider routing defaults.
- Add tests for rounding, missing usage, retries, streaming partials, duplicate request IDs and catalog-version reproducibility.

### P1 — Evidence-backed memory
- Benchmark current Yuki retrieval and context assembly against a fixed, versioned corpus.
- Define a storage-agnostic memory adapter and provenance schema.
- Prototype graph/semantic retrieval only behind the adapter; retain current backend as fallback.
- Evaluate relevance, recall, latency, storage growth, deletion/retention and owner isolation before adoption.

### P2 — Agent evaluation and governed adaptation
- Record accepted outputs and review corrections per task.
- Add fixed regression suites for specialist agents and skills.
- Allow agents to propose changes, but require approval and rollback-capable promotion.
- Keep productivity metrics task-specific and report supervision and cost alongside throughput.

### P3 — Credits discovery
- Start with a read-only catalog importer or manually curated source records; do not scrape aggressively or assume an undocumented API.
- Verify each opportunity against official provider pages and record the verification time.
- Add profile matching and reminders; keep application submission human-approved.

### P4 — Isolation research
- Compare current worker isolation against OS-native sandboxing and capability controls.
- Prototype any alternative in a separate branch with threat model, resource limits, escape tests and independent review.
- Do not treat an experimental agent VM as production isolation until its guarantees are demonstrated on supported platforms.

## 5. Non-goals and safety boundaries

- No foundation-model training from scratch as a consequence of these projects.
- No claims of autonomous recursive self-improvement. Yuki may evaluate and propose bounded changes; promotion remains governed.
- No automatic spending, billing changes, credit applications or sharing of private project data.
- No wholesale replacement of Yuki's durable task runtime with Ruflo, AgentDB or RVM.
- No production dependency adoption until API compatibility, license, security, maintenance, performance and CI evidence are documented.

## Sources

- https://github.com/ruvnet
- https://github.com/ruvnet/ruflo
- https://github.com/ruvnet/RuVector
- https://github.com/ruvnet/metaharness
- https://github.com/ruvnet/rvm
- https://github.com/ruvnet/agentdb
- https://github.com/ruvnet/APx
- https://www.findaicredits.com/
- https://www.findaicredits.com/deals/aws-activate-credits
- https://www.findaicredits.com/deals/google-for-startups-cloud
- https://www.findaicredits.com/deals/claude-for-startups
