# Phase 119 — Real skill runtime integration tests

This phase exercises the approval-gated command against Yuki's actual
`SkillRegistry.defaults()` and built-in `time` skill, rather than only a fake
registry.

The tests verify:
- the real skill is registered and exposes `execute()`;
- an approval-required skill is blocked before explicit approval;
- one-shot approval enables exactly one invocation;
- approval is consumed after invocation;
- unknown names are rejected without fallback imports.

## Run

```bash
python -m unittest tests.test_real_skill_runtime_integration -v
python -m unittest tests.test_approved_skill_invocation -v
python -m unittest discover -s tests -v
```

## Scope

These are command-to-registry integration tests, not proof that every skill is
safe or that the LLM task planner routes through this guarded command. The task
planner integration remains a separate step. The tests construct the default
registry, so integrations must remain safely disabled or configured in CI.
