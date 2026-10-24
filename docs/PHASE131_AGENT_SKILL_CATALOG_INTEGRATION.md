# Phase 131 — Agent skill catalog integration

## What changed

- YukiCoreAgent now exposes discover_skills(task_query) backed by SkillCatalog.
- YukiCoreAgent.run_skill_for_task(task_query, argument) selects the top deterministic catalog match.
- The selected skill still passes through the existing PermissionGate and execution/result handling.
- Empty queries and no-match cases fail closed without asking for approval or executing anything.
- Added tests for discovery without execution, task-driven selection, denied approval, and no-match behavior.

## Agent behavior

1. Search registered skill metadata for a capability relevant to the task.
2. Select the highest-ranked candidate; the catalog does not authorize it.
3. Request explicit approval using the existing PermissionGate.
4. Execute the registered skill and inspect its result.
5. If no suitable skill exists, stop and ask for a more specific task or an explicit skill choice.

## Security boundaries

- The catalog does not import code or change registry membership.
- Catalog guidance is advisory and cannot override policy.
- PermissionGate remains fail-closed when no human approver is configured.
- This is a core-agent API integration; the separate CLI/provider task loop and specialist worker execution paths still need explicit integration and tests.
- Lexical retrieval is deterministic but is not semantic reasoning. Selection quality must be evaluated against a representative task suite before broad rollout.

## Verification

    python -m unittest tests.test_skill_catalog tests.test_agent_skill_catalog_integration -v
    python -m unittest discover -s tests -v

Draft pending CI and review; do not merge before the Phase 130 catalog dependency is integrated.