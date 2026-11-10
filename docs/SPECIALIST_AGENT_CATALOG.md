# Specialist agent catalog

Yuki already has a durable specialist coordinator in
`automation.agent_coordinator`, a task runtime, and a persistent registry.
This catalog adds twelve reviewed default routing profiles without creating a
second coordinator or worker framework.

## Profiles

Research, engineering, debugging, security, testing, documentation,
architecture, DevOps, data, product, UX, and performance.

Each profile has:
- A stable ID and display name.
- A narrow set of routing capabilities.
- A conservative permission-label set.
- The existing allow-listed `emit` action.

## Use

```python
from automation.specialist_agent_catalog import default_specialist_agents
from automation.agent_coordinator import SpecialistAgentCoordinator

coordinator = SpecialistAgentCoordinator(
    runtime,
    database_path,
    default_specialist_agents(),
    authorize=trusted_authorization_callback,
)
```

The coordinator persists initial profiles in its existing registry. Existing
authorization, dependency gating, idempotency, task approval, and worker
boundaries remain in force.

## Important limitations

These profiles are not twelve independently running AI models. They do not
create handlers, grant permissions, or dynamically execute model-generated
code. The application must register trusted worker handlers and explicitly
configure authorization. The default profiles have no shell, repository-write,
production-deploy, secret-read, or merge permissions.

## Tests

```bash
python -m unittest tests.test_specialist_agent_catalog
```
