# Agent capability routing

## Status

The first deterministic capability-routing slice is implemented in
`core/agent_orchestrator.py`. It is an in-process planner/router, not a
distributed worker system and not an autonomous multi-agent runtime.

## Responsibilities

- Register reviewed `AgentSpec` records from trusted application configuration.
- Match planner task capability requirements to explicitly registered agents.
- Prefer the narrowest capability set, with agent ID as a deterministic tie-breaker.
- Preserve task IDs, dependencies, and approval metadata in routed assignments.
- Fail closed when a task has no matching agent.

## Security boundaries

Routing does not execute code, call a model, grant permissions, or invoke a skill.
A routed `skill_name` must still be resolved through the existing skill registry
and executed through Yuki's guarded approval runtime. Agent manifests are
configuration data, not authority. Do not load arbitrary Python plugins as
trusted agents.

## Current limitations

- Registry state is process-local.
- No distributed discovery, worker leases, workload balancing, or cross-process
  communication is implemented by this module.
- `max_concurrent_tasks` is metadata for a future scheduler; this router does
  not enforce it.
- Model selection and provider routing remain responsibilities of the existing
  provider layer.
- A routed plan is not evidence that any task was executed or completed.

## Validation

Run:

```sh
python -m unittest tests.test_agent_orchestrator -v
python -m unittest discover -s tests -v
```
