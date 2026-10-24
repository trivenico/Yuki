# Phase 116 — Skill registry wiring audit

Yuki now provides `/skills audit` to compare Python modules under `skills/` with
explicit imports declared by `skills/registry.py`.

The command reports:
- candidate skill modules (excluding package/registry infrastructure);
- modules directly imported by the registry;
- modules with no direct registry import;
- source parsing/size issues.

## Run

```text
/skills audit
```

## Important interpretation

A module missing a direct import is a review candidate, not automatically a bug.
It may be intentionally loaded by a trusted plugin configuration or another
integration. Conversely, an import does not prove that the class is registered,
its dependencies initialize successfully, or the task runtime can invoke it.
The command does not import candidate modules, change the registry, or register
skills. Use `/skills status` and runtime tests as complementary checks.

## Verification

```bash
python -m unittest tests.test_skill_registry_audit -v
python -m unittest discover -s tests -v
```
