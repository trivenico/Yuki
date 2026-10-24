# Phase 123 — Execution-path guard coverage audit

## Audit scope

This is a source-level audit of the execution paths visible in the inspected repository files and the Phase 122 branch. It is not a claim that every file in the repository has been exhaustively scanned.

## Findings

| Entry point | Current finding | Risk / next action |
|---|---|---|
| Terminal `/tool` command | Phase 122 routes the CLI tool command through `invoke_registered_skill` and a registry built from `yuki_tools.TOOLS`. | Keep the allowlist and local security policy in place; run integration tests. |
| `yuki_tools.run_tool()` | This function remains a direct allowlist-based dispatcher in `yuki_tools.py`. | Any caller other than the guarded CLI can bypass one-shot approval. Search all call sites and migrate or explicitly justify them. |
| `YukiAgent.ask()` | On `main`, this is provider-only text generation and does not execute tools. | Keep model text separate from executable actions. The Phase 121 adapter exists on a draft branch, not on `main`. |
| `YukiAgent.invoke_skill()` | The guarded adapter was proposed in Phase 121, but that PR is not merged. | Do not assume agent-level skill execution is active on `main`. |
| Task planner / specialist workers | No verified evidence in this audit that every worker routes skill/tool calls through the guard. | Treat as unverified; inspect call sites and add tests before enabling execution. |
| Skill registry / plugin loading | The guarded tool adapter wraps only the explicit `TOOLS` allowlist. | It is not automatic discovery and does not make arbitrary Python modules safe to load. |
| External/plugin entry points | No complete call-site inventory was available from the inspected source subset. | Audit separately; require explicit registration, validation, permissions, and the shared execution boundary. |

## Architectural rule

All capability execution should follow:

1. Resolve a name from an explicit registry or allowlist.
2. Validate the argument schema and size.
3. Enforce capability policy and risk-based approval.
4. Consume one-shot approval before side effects.
5. Execute through one guarded interface.
6. Record metadata-only audit outcome and return a bounded result.

Never use model-generated prose as authorization, never import a module because a model named it, and never treat registration as sandboxing.

## What this audit does not claim

- It does not provide a whole-repository numeric count of unguarded call sites.
- It does not prove all task, worker, MCP, or plugin paths are guarded.
- It does not confirm CI passed or any phase PR was merged.
- It does not claim dynamic skill discovery or automatic wiring is implemented.

## Verification commands

```bash
# Find direct execution call sites; review each match rather than blindly replacing it.
grep -RInE '\\b(run_tool|execute|\.run)\\s*\\(' --include='*.py' .
# Run focused guarded-runtime and CLI tests.
python -m unittest tests.test_guarded_skill_runtime tests.test_guarded_tool_adapter tests.test_guarded_tool_cli -v
# Full suite
python -m unittest discover -s tests -v
```

## Next actions

1. Inventory every direct execution call site with file, line, caller, and trust boundary.
2. Migrate eligible task/worker/tool paths to the shared guard.
3. Add regression tests proving denial happens before side effects.
4. Only then add metadata-only skill discovery with explicit review/approval before registration.
