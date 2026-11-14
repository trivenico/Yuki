# Portable runtime import audit

Run the static import inventory before assembling a portable distribution:

```bash
python scripts/portable_import_audit.py --root . --json
```

The report groups imports into Python standard-library modules, repository-local
modules, and third-party **import names**. Use it to review the dependency closure
for each supported launch profile (core, voice, UI, email, integrations).

Important limitations:

- Import names are not necessarily PyPI distribution names (for example,
  `yaml` is provided by `PyYAML`). Map them deliberately; do not guess.
- Imports inside functions are included, so optional/lazy integrations appear.
  They must be assigned to an optional feature profile rather than automatically
  installed into the minimal core runtime.
- Dynamic imports, plugin entry points, subprocess executables, native shared
  libraries, model files, OS frameworks, and runtime data files are not inferred.
- The audit parses source without importing or executing application modules.
  It is an inventory aid, not proof that a wheel is complete or relocatable.

A release pipeline should retain the JSON report as an artifact and compare the
reviewed third-party inventory against pinned, hash-verified lock files for each
platform/profile. Clean-machine launch tests remain mandatory.
