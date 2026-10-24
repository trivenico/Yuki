# Phase 114 — Skill lifecycle status

Yuki's terminal now offers `/skills status` alongside `/skills scan`.

- `/skills scan` reports static candidate modules and parse/size issues without importing candidates.
- `/skills status` compares the candidate-module inventory with the currently configured built-in registry and reports registered skill names.
- Both commands write metadata-only audit events.
- A failed registry inspection fails closed and does not load plugin modules.

## Important distinction

A candidate module name is not necessarily the skill's registered name. This status view intentionally reports both sets separately rather than assuming a match. It does not dynamically enable plugins or alter trust configuration. Plugin registration remains opt-in and restricted to explicitly trusted module names.

## Commands

```text
/skills scan
/skills status
```

## Verification

```bash
python -m unittest tests.test_skill_scan_command tests.test_skill_lifecycle_status -v
python -m unittest discover -s tests -v
```
