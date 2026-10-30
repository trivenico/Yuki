# Yuki agent workforce

## Current starter fleet

The starter registry now contains **100 persisted agent profiles**:

- 99 specialist profiles across research, coding, testing, and reporting.
- One `yuki-coordinator` profile with an explicit delegation allow-list containing the 99 specialists.

These are declarative profiles, not 100 independent foundation models or 100 isolated processes. They currently share four bounded provider-backed text-only worker roles. Specialist profiles define task focus and capability metadata; role handlers still enforce the same no-shell, no-arbitrary-code, no-implicit-network boundary.

## Registration and runtime

`automation.default_agents.starter_agent_specs()` returns the curated profiles.
`seed_starter_agents(SQLiteAgentRegistry)` persists them idempotently and rejects conflicting definitions.
`automation.agent_runtime.build_registered_agent_worker()` composes the persisted registry with the existing provider manager, task runtime, and specialist worker.

The registry and task runtime must share one SQLite database. Provider preferences are explicit; agents without preferences are pinned to the provider currently selected by the user. Cloud use remains disabled by default and requires explicit deployment consent. No cross-provider fallback is performed.

## Important limits

- The 100 profiles are not 100 separately trained models.
- They are not 100 simultaneously running processes.
- The current handler implementation has four text-only roles: research, coding, testing, and reporting.
- The starter profiles have no per-agent concurrency ceiling by default; deployment-level worker capacity and provider rate limits still constrain throughput.
- The coordinator profile's delegation targets are metadata/contract authorization; task submission still requires trusted application authorization and the durable coordinator API.
- Workers do not execute generated code, invoke a shell, modify the workspace, or perform implicit network access.

## Verification

Regression tests assert unique profile IDs, idempotent seeding, the 99-target coordinator allow-list, and construction of a worker handler for every persisted profile. CI must pass before this increment is treated as verified.
