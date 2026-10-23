# Phase 121 — Guarded agent skill adapter

YukiAgent now has an explicit adapter for the centralized guarded skill runtime.

## API

- `configure_skill_runtime(registry, audit=None)` attaches an already constructed registry. It does not discover or import modules.
- `approve_skill_once(name)` only approves names already present in the attached registry.
- `invoke_skill(name, argument)` routes through `core.guarded_skill_runtime.invoke_registered_skill`.

The natural-language `ask()` method remains a provider conversation method. Provider-generated text never triggers skill execution by itself.

## Safety properties

- No configured registry means invocation fails closed.
- Approval is process-local and one-shot.
- Reconfiguring the registry clears pending approvals.
- Skill code still runs with the Yuki process's OS permissions; this adapter is not a sandbox.

## Verification

```bash
python -m unittest tests.test_yuki_agent_skill_adapter -v
python -m unittest tests.test_guarded_skill_runtime -v
python -m unittest discover -s tests -v
```

## Remaining integration

This adapter does not automatically change every CLI, task planner, or worker path. Each caller must explicitly use this API or the shared guarded service, and integration tests must verify that no bypass remains.
