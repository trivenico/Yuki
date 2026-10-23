# Phase 124 — Static execution-path auditor

Adds `scripts/audit_execution_paths.py`, a standard-library AST scanner that
enumerates candidate Python call sites for:
- direct `run_tool(...)` dispatch;
- `execute(...)` and `run(...)` calls that may represent capability execution;
- `exec(...)`, `eval(...)`, and `importlib.import_module(...)` calls.

The scanner skips common generated, dependency, and virtual-environment folders,
and reports file, line, callee, category, and whether the source looks like a test.

## Run against the repository

```bash
python scripts/audit_execution_paths.py . > execution-path-audit.json
```

Review each finding manually. Generic `run()` and `execute()` names create
false positives; aliases, indirect dispatch, reflection, native extensions, and
runtime-generated calls can create false negatives. The output is a candidate
inventory, not a security certification.

## Tests

```bash
python -m unittest tests.test_audit_execution_paths -v
python -m unittest discover -s tests -v
```

## Next step

Run the scanner against the full checkout, triage all results, then route actual
task/worker/plugin capability paths through the guarded runtime. Do not bulk
rewrite calls based on name matching alone.
