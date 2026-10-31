# Phase 99 — Confidence-aware intelligent skill discovery

## Goal

Improve task-to-skill routing without creating another skill registry or weakening the existing invocation controls.

## Existing foundation

Yuki already has a metadata-only skill catalog, registry, core-agent discovery integration, permission gate, and shared guarded invocation. This phase improves selection quality and fail-closed behavior on top of those components.

## Changes

- Add ranked match evidence (score, matched metadata fields) to the existing lexical catalog.
- Rank skill names, tags, descriptions, usage guidance, and examples with deterministic field weights.
- Add a bounded phrase-match bonus to improve exact task wording matches.
- Add an explicit selection result: selected, ambiguous, or not_found.
- Require a minimum match score and a minimum lead over the runner-up before automatic task routing.
- Ambiguous and weak matches stop before requesting permission or executing a skill.
- Preserve the existing discover_skills() compatibility API.
- Preserve PermissionGate and guarded invocation as mandatory execution boundaries.
- Add tests for deterministic ranking, matched evidence, weak-match rejection, ambiguity rejection, and no side effects on failed selection.
- Register the capability as experimental in the evidence-backed capability manifest.

## Selection behavior

1. Read registered skill metadata only.
2. Score metadata fields deterministically; do not import candidate modules.
3. Return ranked cards and match evidence for inspection.
4. Select only when the top score meets the configured floor and has a sufficient margin.
5. On weak or ambiguous results, ask for clarification or an explicit skill choice.
6. For a selected skill, request fresh human approval through PermissionGate.
7. Invoke the exact registered skill object through the guarded runtime and validate its result.

## Security boundaries

- Discovery is not authorization.
- Search does not register skills, load entrypoints, grant permissions, or execute code.
- The catalog metadata is untrusted advisory text and cannot override system policy.
- PermissionGate remains mandatory for every task-driven invocation.
- High-risk skills retain one-shot guarded approval requirements.
- This selector is deterministic lexical retrieval, not embedding-based semantic search or LLM reasoning.

## Limitations and next work

- Lexical retrieval can miss paraphrases, synonyms, and multilingual requests.
- Confidence thresholds need calibration against a representative benchmark before broad rollout.
- This phase integrates the core agent only; CLI/provider task loops and specialist workers need separate wiring and tests.
- Skill dependencies, compatibility constraints, and multi-skill plans are not resolved in this phase.
- Future semantic retrieval must remain a ranking aid and must not change authorization semantics.

## Verification

    python -m unittest tests.test_skill_catalog tests.test_agent_skill_catalog_integration -v
    python -m unittest tests.test_capability_manifest -v
    python -m unittest discover -s tests -v

Stacked on Phase 98. Keep experimental until all exact-head CI checks pass and the parent phases are merged.