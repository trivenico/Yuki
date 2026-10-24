# Phase 120 — Central guarded skill runtime service

Introduces `core.guarded_skill_runtime.invoke_registered_skill` as a reusable
authorization boundary for callers that need to invoke a registered skill.

## Contract

- Only retrieves skills through the supplied registry; it never imports a module.
- Requires one-shot approval when `requires_approval` is true or risk is high/critical.
- Consumes approval before executing, preventing reuse after an exception or failure.
- Bounds skill names, argument length, and returned message length.
- Calls the registered skill's `execute()` interface and normalizes failures.
- Emits metadata-only audit action/status pairs; arguments and result text are not logged.
- Fails closed for unknown skills, invalid arguments, and missing execute interfaces.

## Integration status

This is a reusable service, not yet a claim that every planner, command, worker,
or tool adapter routes through it. Existing execution paths must be migrated and
tested before the system can claim a single enforced execution boundary.
Skill code still runs with Yuki's OS permissions; this service is not a sandbox.

## Verify

```bash
python -m unittest tests.test_guarded_skill_runtime -v
python -m unittest discover -s tests -v
```
