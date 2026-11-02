# Memory Intelligence Engine

The \`memory.intelligence.MemoryIntelligenceEngine\` performs conservative,
read-only analysis of memories in explicitly authorized scopes.

## Example

\`\`\`python
from memory import MemoryIntelligenceEngine

engine = MemoryIntelligenceEngine(vault)
findings = engine.analyze(
    actor_id=authenticated_actor,
    scopes=(("project", "yuki"),),
)
for finding in findings:
    print(finding.kind, finding.confidence, finding.left_memory_id,
          finding.right_memory_id, finding.explanation)
\`\`\`

## Findings

- \`exact_duplicate\`: normalized contents are identical.
- \`possible_duplicate\`: high lexical overlap or embedding similarity.
- \`possible_contradiction\`: opposing terms or negation in similar content.

Every finding is advisory and sets \`requires_human_review=True\`. The engine
never merges, edits, deletes, archives, or changes confidence on source records.

## Optional embedding provider

An application may inject a local or approved embedding callable. Candidate
records are fetched through the vault's explicit-scope ACL filter before
provider invocation. Provider failures and invalid vectors abort analysis;
there is no silent fallback or partial result.

## Operational limitations

- Heuristic contradiction detection is intentionally conservative and can
  produce false positives/negatives; temporal changes and differing contexts
  require human interpretation.
- Lexical similarity is English-token based.
- Candidate comparisons are pairwise and bounded by candidate_limit; worst-case
  comparison cost grows quadratically with candidate count.
- Embedding provider identity, version, retention, region and privacy policy
  remain the application's responsibility.
- Findings are not persisted in this first iteration. The caller should store
  review decisions in an auditable workflow rather than modifying memory.
- Memories and retrieved text remain untrusted data, never executable
  instructions or authorization.

## Tests

\`\`\`bash
python -m unittest tests.test_memory_intelligence
\`\`\`
