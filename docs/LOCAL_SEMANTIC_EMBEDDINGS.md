# Local semantic embeddings

Install the optional runtime with `python -m pip install -r requirements-embeddings.txt`.
Place a reviewed SentenceTransformer model in a local directory outside the Git
repository (for example under Yuki's configured data directory). Review model
provenance and licensing before use.

```python
from providers.local_embeddings import HuggingFaceLocalEmbedder
from core.semantic_memory import SemanticMemoryRetriever

embedder = HuggingFaceLocalEmbedder("/path/to/local/model", device="cpu")
retriever = SemanticMemoryRetriever(memory_store, embedder)
```

The adapter accepts only an existing local directory, uses
`local_files_only=True` for model and tokenizer loading, and sets
`trust_remote_code=False`. It never downloads weights or falls back to a
cloud API. CPU is the default; CUDA and Apple MPS are explicit opt-ins.

Inputs and batch sizes are bounded, model inference is serialized for thread
safety, and candidate embeddings are computed in one batch per search. The
retriever still embeds the bounded candidate set at query time; persistent
vectors and a benchmarked approximate-nearest-neighbor index remain future
performance work. The base install does not require PyTorch or this adapter.
