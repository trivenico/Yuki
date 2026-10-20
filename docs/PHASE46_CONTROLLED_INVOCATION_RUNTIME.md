# Phase 46 — Controlled invocation runtime integration

## Audit findings

The repository had an `InvocationFacade`, but the core agent did not expose it as an invocation API and the CLI had no manifest-invocation command. This made the facade a migration seam rather than a usable runtime path.

## Changes

- Added `YukiCoreAgent.invoke_manifest_skill()`, which delegates to `InvocationFacade`, converts outcomes to safe `AgentResponse` values, and does not expose internal exception details or bearer tokens.
- Added `/invoke SKILL_ID JSON_OBJECT` to the CLI. The review prompt shows the skill ID, risk, exact arguments, payload SHA-256 digest, and expiry; it never prints the bearer approval ID.
- Added `tests/test_phase46_invocation_integration.py` for approved invocation, denial, missing dispatcher, and production fail-closed behavior.
- Production manifest execution remains blocked because no verified OS sandbox backend is enabled. This change does not weaken the Phase 44 production gate.

## Limitations / remaining gaps

- No built-in skill has been migrated to a manifest adapter yet; a dispatcher with registered skills must be explicitly constructed and injected.
- The CLI currently uses a JSON object on one command line and is not a replacement for a richer approval UI.
- Linux Bubblewrap remains a prototype and is not wired into production. Windows/macOS isolation and process-tree/resource limits remain unimplemented.
- Hybrid model routing and cloud-data approval are separate paths and require further policy audit.
- Passing unit tests does not establish OS isolation or production readiness.
