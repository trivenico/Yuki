# Phase 117 — Runtime skill registry visibility

Adds two read-only terminal commands backed by the same `SkillRegistry.defaults()`
factory used by Yuki's built-in skill architecture.

```text
/skills list
/skills inspect NAME
```

- `/skills list` displays registered skill names, risk labels, and descriptions.
- `/skills inspect NAME` displays one skill's metadata and approval requirement.
- Both commands emit metadata-only audit events.
- These commands do not call `run()` or `execute()`, and do not enable dynamic plugin discovery.
- Dynamic plugin loading remains disabled unless configured through the existing explicit opt-in and trusted module allowlist.

This closes part of the visibility gap between static module inventory and the runtime registry. It does not prove task-planner reachability or successful execution; a separate approved invocation path and integration tests are still required.

## Verification

```bash
python -m unittest tests.test_runtime_skill_registry_commands -v
python -m unittest discover -s tests -v
```
