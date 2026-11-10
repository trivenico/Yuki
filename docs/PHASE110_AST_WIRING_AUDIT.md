# Phase 110 — AST-based wiring audit

## Why

The first-pass inventory established that all 48 listed skill module filenames appear
in `skills/registry.py`, but registry inclusion alone cannot answer whether modules
are reachable from runtime entrypoints or whether their external integrations work.
This phase adds a repeatable conservative Python import reachability report.

## Usage

From the repository root:

```bash
python scripts/audit_python_wiring.py . reports/python-wiring-audit.json
python -m unittest tests.test_audit_python_wiring -v
```

The report lists every Python module, the static imports found, the modules reachable
from configured roots, modules needing manual review, and parse errors.

## Classification guidance

- `statically reachable`: reachable through ordinary imports from configured entrypoints, registry, or plugin loader.
- `not reached from configured static roots`: a candidate for manual review, not automatically dead code.
- `test-only`: test source, not runtime application code.
- `package/helper; inspect manually` and `standalone utility; inspect manually`: special cases where import reachability is not a useful deletion signal.

## Known limits

The tool does not execute the code or claim that a skill is operational. It may miss
dynamic imports, configuration-driven modules, command dispatch, reflection, plugin
registration, scripts launched independently, and optional runtime paths. It may
also conservatively mark modules reachable through import edges that are not executed.
No source files are deleted or automatically rewired by this audit.

## Safety and follow-up

Use this report to create reviewed wiring tickets. Before removing any candidate,
verify command routing, configured registries, entrypoint scripts, test discovery,
packaging references, documentation examples, and optional dependency behavior.
