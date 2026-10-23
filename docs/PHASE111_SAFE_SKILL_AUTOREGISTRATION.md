# Phase 111 — Skill discovery and policy-gated auto-registration

## What is automatic

When `plugin_discovery_enabled` is enabled in Yuki configuration, the existing
`SkillRegistry.defaults(...)` path calls `PluginLoader.discover()`. The loader now:

1. Scans Python files in the configured skills package using AST parsing.
2. Identifies candidate modules that syntactically define a `Skill`-like subclass
   with an `execute` method, without importing candidates during the scan.
3. Records candidate names and syntax/size failures in its discovery report.
4. Imports and instantiates candidates only if the module name is in the explicit
   `trusted_plugin_modules` allowlist (or the operator explicitly configured `*`).
5. Registers successfully constructed skills through the existing `SkillRegistry`,
   which checks unique names and callable `execute()`.

The default remains opt-in. Set the discovery flag and trusted module list in
configuration to enable auto-registration. A discovered file is not automatically
trusted, and a random Python file is not automatically wired into the application.

## Why this is not unrestricted auto-wiring

Python import executes module code with Yuki's process privileges. AST inspection is
only a preflight, not a security sandbox or proof of safe behavior. Automatically
importing every newly discovered file would allow a dropped or compromised module to
execute arbitrary code. Therefore, safe auto-registration requires explicit trust,
review, and existing permission gates. It does not bypass per-skill approvals.

## Current limits

- Plugin classes must have zero-argument constructors for the existing loader path.
  Skills requiring dependencies must be wired through an explicit factory/adapter.
- Discovery targets Python modules in the configured package, not every file type in
  the repository. Non-Python files need a declared integration manifest/adapter.
- Static scanning may miss aliased bases, decorators/factories, dynamically created
  classes, or skill implementations split across files.
- Candidate reports are currently exposed by the loader result/last_report; a future
  runtime status command should surface that report to users.
- This does not dynamically patch arbitrary call sites or imports. It registers a
  validated skill with the existing registry, which is the supported runtime wiring
  mechanism.

## Verification

```bash
python -m unittest tests.test_skill_catalog tests.test_plugin_loader_static_discovery -v
python -m unittest discover -s tests -v
```
