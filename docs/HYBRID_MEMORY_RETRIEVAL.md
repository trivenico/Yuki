# Hybrid Memory Retrieval

\`memory.hybrid_retrieval.HybridMemoryRetriever\` combines bounded lexical
retrieval with optional embedding similarity. It uses the collective vault's
ACL-filtered candidate API, so vector ranking cannot expand the caller's
authorized scopes.

## Lexical-only mode

\`\`\`python
retriever = HybridMemoryRetriever(vault)
results = retriever.search(
    actor_id=authenticated_actor,
    query="release rollback procedure",
    scopes=(("project", "yuki"),),
)
\`\`\`

## Inject an embedding provider

\`\`\`python
def embed(text: str) -> list[float]:
    # Call a locally hosted or approved embedding provider here.
    # The provider's model/version and data-handling policy are application-owned.
    return embedding_client.embed(text)

retriever = HybridMemoryRetriever(
    vault,
    embed=embed,
    lexical_weight=0.35,
    candidate_limit=500,
)
\`\`\`

The final score is a weighted combination of lexical Jaccard overlap and
cosine similarity normalized to [0, 1]. Set lexical_weight to 1 for lexical
ranking or 0 for semantic-only ranking. The default is 0.35.

## Security and reliability

- Candidate records are fetched only from explicit scopes that pass
  \`memory:read\` authorization before any embedding call.
- No embedding SDK, model, network client, or API key is bundled.
- Embeddings must be finite numeric vectors with matching dimensions.
- Provider failures fail closed; the retriever does not silently change
  ranking modes or return partial semantic results.
- Candidate count, vector dimensions, query length, and result count are bounded.
- Treat embedding providers as data processors: review retention, region,
  privacy, and whether project content may be sent to them.
- Embedding model/version changes can change ranking. Persist provider identity
  and model version in the application configuration and re-index when needed.
- Initial implementation embeds candidates at query time; production deployments
  should add a versioned, invalidatable vector cache/index after measuring
  corpus size and latency.

## Limitations

The current lexical tokenizer is English-centric. There is no automatic
memory ingestion, reranker, vector database, background indexing, or
cross-device synchronization in this module. The existing vector memory
implementation is not replaced.

## Tests

\`\`\`bash
python -m unittest tests.test_hybrid_memory_retrieval
\`\`\`
