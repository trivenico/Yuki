# Yuki Local Knowledge Engine

The local knowledge engine stores reference documents independently from personal memory.

## Storage boundary

- Personal memory remains in the consent-controlled memory service.
- Knowledge sources and chunks live in a separate SQLite database (default: `YukiData/knowledge.sqlite3`).
- Model weights and training datasets are separate resources; this engine does not modify either.
- The default `YukiData/` directory is excluded from Git. Never commit private documents or local indexes.

## Supported sources

The first implementation accepts explicitly selected UTF-8 text files: TXT, Markdown, reStructuredText, Python, JSON, YAML and YML.

It does not crawl directories, download URLs, execute source code, or parse PDFs/DOCX yet. Those require separate opt-in ingestion adapters and their own security review.

## Example

```python
from knowledge import KnowledgeEngine

engine = KnowledgeEngine()  # YukiData/knowledge.sqlite3
result = engine.ingest_file(
    "docs/architecture.md",
    workspace="/path/to/approved/workspace",
)
print(result)

for match in engine.search("local inference architecture", limit=5):
    print(match.source_name, match.source_path, match.content)
```

## Safety and limits

- A caller must explicitly select a file and approved workspace.
- Files outside the workspace and symbolic links are rejected.
- Supported extensions are allowlisted.
- File size is capped at 2 MB and decoded document text at 200,000 characters.
- Text is split into overlapping chunks of at most 2,000 characters.
- SQLite parameter binding is used for all values; search terms are tokenized and quoted before FTS5 matching.
- Search returns source IDs, source names, source paths, chunk IDs and relevance scores for provenance.
- Unchanged source content is detected by SHA-256 and is not re-indexed.
- Re-indexing changed content replaces its prior chunks transactionally.
- Source deletion removes its chunks and full-text index rows.
- SQLite FTS5 is required. No embedding model, external AI API, network call, or training operation is used.

## Current limitations

This is a local lexical retrieval foundation, not a complete RAG pipeline. It does not yet perform semantic embeddings, OCR, PDF/DOCX extraction, web crawling, claim verification, freshness evaluation, or automatic knowledge-graph extraction. Retrieved content must be treated as untrusted evidence by downstream reasoning and must never authorize tools or override policy.
