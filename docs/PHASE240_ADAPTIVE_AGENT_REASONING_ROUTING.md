# Phase 240 — Adaptive Reasoning in Specialist Routing

**Status:** Reasoning budget policy integrated into advisory task routing; CI verification pending.

## What changed

Yuki's existing task router already classifies requests, asks the authorized coordinator for the actor's enabled specialists, filters by required capabilities and optionally recommends reviewed model candidates. It intentionally does not dispatch work or activate models.

This phase connects that route recommendation to the bounded reasoning effort policy introduced in Phase 239.

The route now includes a `reasoning_budget` with:
- Profile name.
- Maximum model turns.
- Maximum guarded tool calls.
- Maximum execution duration.
- Maximum transcript characters.
- Policy rationale.

Coding routes default to high effort. Reasoning/research routes default to high effort. General and ambiguous conversation routes default to standard effort. Callers can request an explicit allow-listed effort profile.

A caller may pass local device ceilings. Those ceilings intersect with the policy budget using minimum values; they can reduce limits but cannot expand them. Invalid effort names and unknown device-limit fields are rejected.

## Security and execution boundary

This is still an advisory route. The router returns candidate specialists and resource budgets only. It does not submit a plan, dispatch a worker, start a model, grant tools or change approvals.

At execution time, the trusted service must pass the returned values to the existing `AgentExecutionLoop` (or a stricter worker-level budget) and retain all independent worker, provider, operating-system and policy ceilings. A caller-supplied device limit is not a trusted hardware attestation.

The existing agent loop validates its own hard bounds. This phase does not weaken them.

## Agent scale strategy

Yuki can maintain hundreds of specialist definitions and route tasks to the right subset. That is not equivalent to running hundreds of independent model processes. On a single laptop, the scheduler should cap concurrent inference based on verified available memory, CPU/GPU capacity, queue pressure and model residency. Specialist agents should be lightweight task roles sharing a small number of admitted model runtimes when safe.

## Regression coverage

Router tests cover default coding effort, device-ceiling intersection, rejection of unsupported effort names, authorization-scoped agent listing and the existing no-dispatch/approval contract.

## Not delivered yet

- No automatic task dispatch.
- No change to coordinator approval semantics.
- No proof that the downstream worker consumes this route budget.
- No autonomous model training or checkpoint promotion.
- No GPU probe or multi-device agent execution.
- No 100-way concurrent inference guarantee.

Next integration must carry the budget in a trusted execution request from the approved plan through worker admission into `AgentExecutionLoop`, then persist selected and actual resource usage for evaluation.
