# Yuki wiring audit — first-pass static inventory

Audit date: 2026-10-23
Repository: trivenico/Yuki
Reference: default branch `main` at `bf8a15f09aa4b6eb48ce521823e8fdbf4e847ea8`

## Inventory counts

The recursive Git tree reported 358 tracked files total, including 242 Python files and 77 Python test files. The `skills/` package contains 48 non-helper Python modules in this inventory; all 48 filenames are referenced by `skills/registry.py`. This is evidence of registry-level wiring, not proof every capability is operational or externally integrated.

## Confirmed scaffold/adapter-only capabilities

At least these 8 skill modules explicitly document limited/manual behavior or require adapters before live external actions:

1. `skills/long_video_generator.py` — creates outlines/checklists; does not render media.
2. `skills/video_generator.py` — provider-backed rendering is not enabled by the manifest/wrapper alone; verify configured provider path before claiming live rendering.
3. `skills/smart_home_full.py` — fails closed without a reviewed vendor adapter; status-only when adapter exists.
4. `skills/car_integration.py` — readiness wrapper; no vehicle connection by default.
5. `skills/financial_manager.py` — manual local ledger; no bank sync, payment, or trading.
6. `skills/home_security.py` — manually supplied event log; no live camera/alarm monitoring.
7. `skills/messaging_expanded.py` — local drafts/outbox; no external message delivery.
8. `skills/device_scanner.py` — analyzes user-supplied inventory; does not probe the network.

These are intentionally bounded implementations, not necessarily defects. They should be labelled as scaffolds/limited capabilities in product UX until adapters and integration tests exist.

## Additional integration risks observed

- `core/plugin_loader.py` imports Python code with Yuki's process privileges; it is not a sandbox. Discovery is disabled by default.
- `skills/discovery.py` is an opt-in helper; the default registry is explicitly constructed. Automatic discovery must not be represented as always active.
- `core/mcp_client.py` is an HTTP MCP client primitive; full stdio launch and runtime orchestration should not be claimed from this file alone.
- Main runtime entrypoint `main.py` builds the explicitly configured skill registry and permission gate. New modules need call-site/runtime wiring plus tests, not just imports.
- The new sync journal, conflict bridge, pairing store, and authenticated transport PRs are draft branch work, not part of `main` and not active in the running application.

## Phase 101–109 sync stack status

The sync stack remains unmerged and is not connected to Yuki startup/runtime. The recent PRs provide local primitives for journaling, conflict reconciliation, opt-in conflict events, pairing, and request authentication. They do not yet form a complete device-to-device protocol.

## Counting limitation

This is a first-pass static inventory, not a claim that exactly eight files are unwired across the entire repository. The eight are confirmed capability-limitation files. Establishing a defensible total for every unused/unreferenced Python file requires an AST-based import/call graph, runtime entrypoint tracing, and a classification of intentional helpers, tests, optional modules, and dead code. Text search alone can misclassify dynamic imports and registry wiring.

## Recommended next audit pass

1. Build an AST import and symbol-reference report from `main.py`, `yuki.py`, the default registry, and workflow entrypoints.
2. Compare every `skills/*.py` module against registry construction, CLI/UI reachability, and integration tests.
3. Classify files as runtime-wired, registry-only, optional dependency, adapter/scaffold, test-only, or apparently unreachable.
4. Produce exact file lists and remove or wire genuinely dead code in separate reviewed PRs.
