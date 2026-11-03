# Approval-first evolution planner

`EvolutionPlanner` is the first planning boundary for supervised repository
self-improvement. It accepts a bounded structured proposal, validates it, and
uses the existing `SpecialistAgentCoordinator` for capability-based routing
and durable idempotency.

## Example

```python
from automation.evolution_planner import EvolutionPlanner, EvolutionProposal
from automation.agent_coordinator import SubtaskSpec

planner = EvolutionPlanner(existing_coordinator)
proposal = EvolutionProposal(
    objective="Improve repository test reliability",
    request_id="reliability-upgrade-2026-01",
    subtasks=(
        SubtaskSpec(
            subtask_id="inspect-tests",
            description="Inspect test reliability and identify flaky tests",
            required_capabilities=("research",),
        ),
    ),
)
planned = planner.plan(actor_id=authenticated_actor_id, proposal=proposal)
assert planned.approval_required
assert not planned.dispatch_started
```

## Guarantees

- The objective is bounded text and receives a SHA-256 digest.
- Request IDs are bounded and cannot contain path separators.
- Plans contain 1-20 validated specialist subtasks.
- Approval is forced on every evolution subtask, regardless of a weaker caller
  supplied flag.
- Existing coordinator authorization, capability routing, dependency
  validation, and durable idempotency remain authoritative.
- Repeating the same request returns the same persisted plan.
- Reusing a request ID with a changed objective produces a distinct plan.
- Planning never calls the coordinator's dispatch/advance operation.

## Deliberate non-goals

This component does not execute agents, approve tasks, edit files, run tests,
create branches, open pull requests, merge, or deploy. Those operations must
remain separate, explicitly authorized stages. In particular, the existing
engineering test runner is not a sandbox; untrusted code must not be run in the
Yuki process environment.

## Validation

`tests/test_evolution_planner.py` covers approval enforcement, no-dispatch
planning, idempotency, changed objectives, authorization failures, and invalid
input. Run with:

```bash
python -m unittest tests.test_evolution_planner tests.test_agent_coordinator
```
