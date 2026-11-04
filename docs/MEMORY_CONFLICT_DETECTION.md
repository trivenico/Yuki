# Yuki memory conflict detection

MemoryConflictDetector identifies possible contradictions among records the requesting owner and agent are already authorized to read.

## Safety contract

- Uses AgentMemoryStore.list_memories as the only candidate source. Actor, agent, shared-scope, expiry and read-permission filtering happen before comparison.
- Does not call a model, rewrite memory, delete records, change confidence or choose a winning claim.
- Emits needs_owner_review candidates only; both claims and their source, agent, verification state, confidence and timestamps are retained.
- Retrieved claim text is untrusted reference data and must never be executed as instructions.
- Shared claims can originate from different agents. Both source agent IDs are included in the claim records.
- Contextual differences (for example, staging versus production) should not be collapsed into a contradiction by this lexical detector.

## Detection signals

The initial detector is deliberately conservative and deterministic:
- Explicit opposite state terms such as enabled/disabled, allowed/denied and online/offline.
- Opposing negation over a sufficiently overlapping subject.
- Different numeric measurements using the same unit token and an overlapping subject (for example, a 30-second versus 60-second timeout). Different unit spellings are not converted or compared.
- Subject overlap threshold is configurable between 0.5 and 1.0.

This is a candidate generator, not a natural-language truth engine. It can miss paraphrases, temporal changes, numeric conflicts and complex context. It may also produce false positives. A later local-model adjudicator can propose a reason and context comparison, but must preserve the same owner-review boundary.

## Usage

```python
from core.agent_memory_conflicts import MemoryConflictDetector

detector = MemoryConflictDetector(memory_store)
candidates = detector.detect(
    actor_id=owner_id,
    agent_id="research",
    include_shared=True,
    min_overlap=0.75,
)
```

No candidate is automatically promoted, merged or removed. The owner must inspect the evidence and decide whether claims describe different contexts, whether one is outdated, or whether a correction/replacement is warranted.
