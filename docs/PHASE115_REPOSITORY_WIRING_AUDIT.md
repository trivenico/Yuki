# Phase 115 — Static repository wiring audit

Yuki now exposes `/wiring audit`, backed by `core.repo_wiring_audit.audit_python_wiring`.

The audit inventories Python files, parses imports statically, and groups files into:
- possible entry points;
- statically imported modules;
- files with no static inbound import;
- tests/examples;
- files needing inspection due to parsing or size issues.

It excludes common generated/environment directories and bounds individual source files to 1 MiB. Importer lists are capped in the human-readable command output.

## Run

```text
/wiring audit
```

## Interpret results carefully

A file with no static inbound import is a review candidate, not proof that it is dead or unwired. Runtime loading may happen through `importlib`, registries, decorators, manifests, entry points, reflection, external launchers, or non-Python integrations. This report does not prove end-to-end task-runtime reachability and does not modify code or wire files automatically.

## Tests

```bash
python -m unittest tests.test_repo_wiring_audit -v
python -m unittest discover -s tests -v
```
