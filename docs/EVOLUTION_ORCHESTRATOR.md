# Evolution Orchestrator

`automation.evolution_orchestrator.EvolutionOrchestrator` sequences the
existing Yuki evolution adapters into a supervised lifecycle:

1. Validate the requester and task.
2. Ask the planner for a bounded plan and verify its canonical SHA-256 digest.
3. Request explicit approval for that exact plan.
4. Invoke the configured executor only after literal boolean approval.
5. Validate execution identity and commit SHA.
6. Obtain independent review and commit-bound verification evidence.
7. Record every lifecycle transition in the required ledger.
8. Return either `ready_for_human_merge_review` or `blocked`.

## Adapter contracts

- `planner(actor_id, task) -> EvolutionPlan`
- `approval_verifier(plan) -> bool`: must authenticate approval for the exact
  plan digest, requester and intended action. Only literal `True` is accepted.
- `executor(plan) -> EvolutionExecution`: must invoke the approved sandbox
  supervisor and return evidence for the resulting commit.
- `reviewer(plan, execution) -> ReviewEvidence`
- `verification_provider(plan, execution) -> VerificationReport`
- `ledger(event) -> None`: must durably append the event or raise.

The orchestrator does not implement authentication, sandbox isolation, CI
attestation, AI inference, GitHub merging, or deployment. Each adapter must
connect to the trusted implementation of that capability. Do not wire a
model-generated approval callback or agent-supplied CI claims into this API.

## Safety properties

- Fails closed on missing/invalid adapters and ledger errors.
- Requires a plan digest computed from canonical JSON.
- Rechecks the plan digest after approval and after execution to detect
  mutation by callbacks.
- Requires explicit approval; no default or implicit approval.
- Stops on planner, approval, executor, review, verification or ledger failure.
- Review and verification evidence must match the exact execution commit.
- A positive result permits only a human merge review proposal. It does not
  merge, publish, or deploy.

## Tests

```bash
python -m unittest tests.test_evolution_orchestrator
```

The tests use deterministic fakes. They do not prove adapter authenticity,
container isolation, external CI attestation, or production durability.
