# Phase 113 — Runtime skill scan command

Yuki's terminal now exposes `/skills scan` to inspect Python skill candidates from
the local `skills/` package.

The command uses the AST preflight in `core.plugin_loader` and reports candidate
module names plus source parse/size issues. It does not import candidate modules,
register them, or execute their code. The scan action writes a metadata-only audit
event.

## Use

```text
/skills scan
```

A candidate is not the same as a trusted, registered, or runnable skill. Automatic
registration remains governed by the existing `plugin_discovery_enabled` setting,
the explicit trusted module allowlist, constructor compatibility, duplicate-name
validation, and the normal capability permission gate.

## Verification

```bash
python -m unittest tests.test_skill_scan_command -v
python -m unittest discover -s tests -v
```
