# Yuki Intelligence Orchestrator

The orchestrator turns a model-proposed task DAG into a validated, durable
specialist plan. It composes existing systems rather than replacing them.

## Boundaries

- The model returns JSON task proposals only; it receives no tools.
- The proposal schema is exact and bounded. Unknown fields are rejected.
- `Planner.validate_tasks` checks identifiers, capabilities, dependency
  references, duplicate IDs, graph cycles, and maximum plan size.
- `SpecialistAgentCoordinator.submit` performs authoritative capability and
  permission routing and persists the plan idempotently.
- `dispatch_ready` delegates to the existing coordinator. The workflow
  runtime's approval and execution controls remain authoritative.
- This component never executes tools, grants permissions, approves work, or
  installs/enables specialist agents.

## Integration

Inject a model callable compatible with Yuki's structured provider adapter.
Use the trusted authenticated actor ID and a server-generated idempotency key.
The model must not choose actor IDs, agent IDs, permissions, or tool definitions.
