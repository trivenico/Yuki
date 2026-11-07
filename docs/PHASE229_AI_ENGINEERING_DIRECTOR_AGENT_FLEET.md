# Phase 229 — AI engineering director and agent-fleet scaling

**Status:** architecture baseline plus first concurrency enforcement  
**Purpose:** make Yuki an AI engineering system that can coordinate a large specialist fleet, not a chatbot that merely names agents.

## Product invariant

Yuki owns the goal, planning, delegation policy, memory, evidence review and final delivery. Specialist agents are replaceable workers. Model-generated plans are untrusted proposals; deterministic policy, authorization, worker readiness, resource limits and human approval remain authoritative.

## Target architecture

1. **Yuki Core / Chief Intelligence:** identity, long-term memory, context retrieval, goal understanding, planning, decision records and final synthesis.
2. **AI Engineering Director:** converts goals into acceptance criteria, architecture decisions, work packages, dependency DAGs, risk classifications and verification plans. It delegates implementation, research, QA, security, operations and documentation to specialist teams.
3. **Fleet Registry:** versioned agent manifests with capability, permission, risk, runtime requirement, model/provider, contract, timeout and concurrency metadata. Registration is metadata only; it does not install code or grant permissions.
4. **Hierarchical orchestration:** director -> domain leads -> specialist agents -> bounded tasks. Each level has explicit task-count, depth, token, time, cost and concurrency ceilings. Child plans inherit the parent's scope and cannot broaden permissions.
5. **Scheduler and worker pool:** readiness-aware routing, per-agent and per-provider concurrency, fair queues, backpressure, leases, cancellation, retries with idempotency, resource-aware placement and crash recovery.
6. **Independent verification:** reviewers and test/security agents produce evidence; implementation agents cannot approve their own high-risk output.
7. **Shared knowledge plane:** versioned task artifacts, source citations, agent memory, decisions, provenance and retrieval. Treat all agent-produced content as untrusted until validated.
8. **Governance and observability:** approval gates, permission checks, budget accounting, trace correlation, audit events, fleet health, queue depth, latency, failure and cost dashboards.

## What exists now

- core/intelligence_orchestrator.py: model-assisted bounded DAG proposal; text-only planning call; no tools; all proposed tasks require approval.
- automation/agent_coordinator.py: deterministic capability/permission routing, durable plans, dependency gating, task idempotency, contract validation, recovery, handoff audit and trace hooks.
- SpecialistAgent: version, risk tier, timeout and max-concurrency metadata.
- core/planner.py: deterministic planner and DAG validation.
- Workflow/task runtime: durable queue, worker leases, authorization, retries/recovery controls and bounded worker execution.
- This phase adds a per-agent active-task count before dispatch, using the registered max_concurrency limit, and a regression test that verifies a second task remains pending until capacity is free.

## Honest current limits

- The model-assisted director currently accepts at most 10 tasks per proposal.
- The coordinator accepts at most 20 subtasks per plan.
- The new concurrency gate uses an in-process lock. It prevents concurrent dispatch races through one coordinator instance, but is **not yet a distributed semaphore** across multiple coordinator processes or hosts.
- Registry manifests can describe large fleets, but no benchmark currently demonstrates hundreds of simultaneously executing agents.
- Provider quotas, global model token budgets, per-tenant fairness, distributed scheduling, hierarchical subplan persistence and cross-host capacity accounting remain future work.
- Agent max_concurrency is now enforced within one coordinator instance during dispatch; it must not be represented as a global fleet-wide guarantee.
- Current worker execution is bounded by configured workers and actual available compute. A large registered fleet does not mean all agents run simultaneously.

## Scale targets and safety ceilings

Design for a registry of hundreds of agent identities while starting with conservative runtime concurrency. Capacity should be a policy value derived from available workers, provider rate limits, memory/CPU/GPU, task risk and budget—not a hard-coded promise.

Suggested initial deployment profile (configuration target, not current measured capacity):
- 500 registered agent manifests.
- 20 subtasks per persisted plan.
- 10 tasks per model-generated planning proposal.
- 32 global active worker tasks across a single deployment, subject to worker and provider capacity.
- Per-agent concurrency default 1, explicit reviewed overrides.
- Maximum hierarchy depth 3 until recursive delegation is threat-modelled and tested.
- Every task has bounded wall time, output bytes, context/token budget and cost ceiling.

A future distributed scheduler must reserve capacity atomically, expire/reconcile leases safely, enforce global/provider/tenant budgets, and expose queue/backpressure metrics. Do not simply raise task-count limits to simulate scale.

## Required roadmap

1. Complete local concurrency gate: verify all OS CI; ensure terminal task reconciliation frees capacity and cancellation releases slots.
2. Durable distributed capacity ledger: transactional slot reservations, lease expiry/reconciliation and multi-process race tests.
3. Hierarchical team plans: persist parent-child plan links, depth/descendant budgets, inherited scope and cycle prevention.
4. AI Engineering Director: explicit engineering phases: discover, requirements, architecture, plan, implement, test, security review, release, observe and learn.
5. Provider/resource scheduler: per-provider token/request ceilings, worker capability matching, fair queues and backpressure.
6. Agent memory and knowledge: permission-scoped shared artifacts with provenance, retention and prompt-injection boundaries.
7. Independent quality review: separate reviewer identities, evidence requirements and disagreement escalation.
8. Desktop and CLI fleet control: register/disable agents, inspect teams, approve plans, monitor runs, cancel/recover, view traces, costs and deliverables.
9. Load testing: synthetic workload tests for hundreds of registered agents and controlled concurrency; separately benchmark real worker execution before claiming scale.

## Acceptance criteria before claiming a 100-agent operating fleet

- 500 manifest registrations and retrieval without performance regression.
- Multi-process scheduler tests prove the configured global and per-agent concurrency ceiling cannot be exceeded under races.
- 100 concurrent queued tasks with bounded backpressure and no lost/idempotency-duplicated work.
- Lease expiry, worker crash, cancellation and restart tests preserve audit history and do not duplicate side effects.
- Provider budgets and rate limits enforced before dispatch.
- All tasks trace to parent goal, agent identity/version, worker identity, approvals and evidence.
- A task is not complete until acceptance criteria and independent verification pass.
- UI shows registered, ready, queued, running, blocked, failed and disabled states truthfully.

## Safety rule

Yuki can autonomously plan and delegate within approved permissions and resource budgets. It cannot grant itself new permissions, bypass approvals, execute arbitrary model-generated code, train on private memory by default, or promote its own software/model upgrades without independent verification and required human authorization.
