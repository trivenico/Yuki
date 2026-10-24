# Phase 130 — On-demand skill catalog

## Purpose

Skills are discoverable capabilities that help agents use tools effectively. This phase adds a read-only searchable catalog over an existing trusted registry. Agents can search by task language, inspect the selected skill's usage and guidance, then invoke it through the shared guard.

## Catalog metadata

- Name and description for discovery.
- Tags, usage guidance, examples, and bounded agent-specific instructions.
- Risk and approval indicators to inform planning.
- Deterministic lexical ranking with bounded results.

## Agent use pattern

1. Translate the task into a short capability query.
2. Search the catalog and inspect the best matching skill cards.
3. Check requirements, permissions, risk, and whether clarification is needed.
4. Invoke only the selected registered skill via SkillCatalog.invoke().
5. Validate the result against the task; retry only when safe and useful.
6. Ask for approval before consequential high-risk actions.

Search is discovery only; it is not authorization. Invocation delegates to invoke_registered_skill and retains its one-shot approval behavior.

## Security and limits

- The catalog does not dynamically import skill entrypoints or grant permissions.
- Guidance is untrusted metadata and must never override system policy, access control, or tool authorization.
- Metadata is bounded before being exposed to the planner.
- Search is lexical and deterministic; semantic retrieval can be added later behind evaluation tests.
- This phase does not yet wire the catalog into the default planner, specialist agents, or startup. It provides the tested catalog API for that integration.

## Verification

    python -m unittest tests.test_skill_catalog -v
    python -m unittest discover -s tests -v

Draft pending CI and review; no production readiness claim is made by this phase.