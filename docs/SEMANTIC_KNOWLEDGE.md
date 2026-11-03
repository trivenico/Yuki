# Local Semantic Knowledge Retrieval

Yuki's semantic layer is optional and remains separate from personal memory, the lexical knowledge store, and model training.

## Components

- `knowledge/embeddings.py`: adapter around a locally cached Sentence Transformers model.
- `knowledge/vector_store.py`: SQLite vector persistence and cosine-similarity retrieval.
- `knowledge/hybrid_search.py`: reciprocal-rank fusion of lexical and semantic candidates.
- `knowledge/engine.py`: canonical document/chunk metadata and provenance.

## Install

The default Yuki installation does not require an embedding model or download. Install optional dependencies only when enabling semantic retrieval:

```bash
pip install -r requirements-embeddings.txt
```

Download/cache the selected Hugging Face model separately in an explicit setup step. Runtime loading uses `local_files_only=True`; it will fail clearly rather than silently download weights or access the network.

Example model candidates should be evaluated for language coverage, license, embedding quality, latency and memory footprint before choosing a default. The adapter accepts any locally cached compatible Sentence Transformers model.

## Vector index behavior

- Embeddings are L2-normalized and stored as float32 blobs in SQLite.
- Search computes cosine similarity by dot product over normalized vectors.
- Model IDs and dimensions are part of the retrieval filter, preventing accidental comparison across incompatible embedding spaces.
- Source deletion can remove vectors by source ID.
- Hybrid search uses reciprocal-rank fusion and resolves semantic chunk IDs against the canonical knowledge database before returning content. Stale IDs and mismatched source IDs are ignored.
- This initial implementation uses exact scan retrieval, not an approximate nearest-neighbor index. It is suitable for a local MVP and should be benchmarked before large-scale use.

## Security and trust

Retrieved chunks are evidence, not instructions. Hybrid search does not grant tool access, change system policy, or authorize actions. Callers must pass returned content to reasoning as untrusted reference material and preserve source attribution. Only ingest explicitly approved local sources.

## Current limitations

This is an optional semantic retrieval foundation, not model training or autonomous self-improvement. It does not download models at runtime, fine-tune weights, crawl websites, or verify factual claims. A later semantic indexing service should coordinate source re-indexing and vector cleanup; do not assume deleting a lexical source automatically deletes its vector records yet.
