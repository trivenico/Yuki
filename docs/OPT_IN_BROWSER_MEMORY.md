# Opt-in browser memory

The authenticated browser runtime keeps semantic memory disabled by default.
Enable it only after placing a reviewed SentenceTransformer model on disk:

```bash
python -m pip install -r requirements-embeddings.txt
export YUKI_MEMORY_ENABLED=true
export YUKI_EMBEDDING_MODEL_PATH=/absolute/path/to/reviewed-local-model
export YUKI_EMBEDDING_MODEL_ID=sentence-transformer-name@immutable-version
export YUKI_EMBEDDING_DEVICE=cpu
```

Then start the browser runtime normally with `python scripts/run_web.py`.
Use a stable, versioned model ID; changing embedding models creates a separate
vector namespace and prevents stale vectors from being reused. CPU is the
default. CUDA and Apple MPS are explicit opt-ins.

The bootstrap uses the authenticated account ID supplied by
`ConversationService`, the owner-bound memory authorizer, and a persistent
SQLite database at `YUKI_WORKSPACE/agent-memory.sqlite3`. Shared memories are
visible only when the current account has the separate sharing permission.
Retrieved content remains untrusted reference data and cannot grant tools or
override system policy.

The model loader is local-only (`local_files_only=True`,
`trust_remote_code=False`); it never downloads model weights or calls a cloud
embedding service. If memory is enabled but the path, model identity, optional
dependency, or model initialization is invalid, startup fails rather than
silently disabling memory or weakening authorization.

This integration enables retrieval context only. It does not yet provide
automatic memory extraction/writes, a browser memory-management interface,
or a configurable provider-specific token budget. Those require separate
reviewed features. Disable the feature by setting
`YUKI_MEMORY_ENABLED=false` and restarting Yuki.
