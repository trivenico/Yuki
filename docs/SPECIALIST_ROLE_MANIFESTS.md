# Versioned specialist role manifests

The coordinator's `specialist_agents` table remains the canonical registry. This feature extends its existing records; it does not create a second registry or task queue.

## Manifest fields

Each specialist record now carries:
- `department`, `role_version`, and `owner` for organizational identity and accountability;
- `handler_id` and `handler_ready` to distinguish a role description from a reviewed executable handler;
- `max_steps` and `max_tokens` as declared resource ceilings for a future worker integration;
- `approval_class` (`read_only`, `review_required`, or `human_approval`).

The coordinator rejects routing to a specialist unless it is both enabled and handler-ready. Legacy records are migrated to the existing `emit` handler to preserve their current behavior.

## Starter roles

`automation/agent_roles.py` defines six starter slots:
- Chief of Staff (`EXEC-01`)
- Research Analyst (`RES-12`)
- Software Engineer (`ENG-03`)
- QA / Evals Engineer (`ENG-16`)
- Security Reviewer (`SEC-02`)
- Technical Writer (`PROD-09`)

They are created disabled, with no permissions, no handler, and `handler_ready=False`. `register_initial_role_slots` persists them only when called by an authorized coordinator actor. They cannot be routed or dispatched.

## Important limits

This is registry and governance groundwork, not six functioning agents. The current specialist worker only supports the bounded `emit` action; it does not yet invoke role-specific models or tool adapters. Resource ceilings are recorded as metadata but are **not yet enforced as token/compute budgets** by the worker. Approval class is descriptive metadata and does not replace the existing per-task approval/session authorization flow. A role must remain disabled until a concrete handler, enforcement path, tests, and review are implemented.

Do not set `handler_ready=True` merely to make a role routable. Handler readiness is a release gate, not an administrative convenience.

## Existing provider-backed handler adapters

The existing `automation.specialist_worker` already provides bounded text-only provider handlers for research, coding, testing, and reporting. `build_initial_role_handlers(provider_manager)` maps those implementations to RES-12, ENG-03, ENG-16, and PROD-09 and returns the mapping expected by `SpecialistTaskWorker`.

This is an adapter factory, not activation. It does not register handlers in the coordinator, change permissions, enable roles, or set `handler_ready=True`. Chief of Staff and Security Reviewer do not yet have matching role-specific implementations in this adapter map. All six role records remain disabled until a separately reviewed deployment composition wires the provider, worker, task authorization, and lifecycle controls together.
