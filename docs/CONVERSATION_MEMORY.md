# Conversation memory store

`automation.conversation_memory.ConversationMemoryStore` provides an opt-in, owner-scoped store for imported conversation records.

## Trust and privacy guarantees

- Import requires `consent=True` and the `memory:import` authorization decision.
- Every operation is owner-scoped and independently authorized.
- Source provider, stable source record ID, role, timestamps, and SHA-256 content digest are retained.
- Duplicate imports are ignored using a unique owner/source/record key.
- Imported content is inert reference data; it is never executed or treated as trusted instructions.
- This component performs retrieval only. It does not train, fine-tune, or update model weights.
- Users can search, delete one record, or clear all records for their owner scope.
- Input is bounded to 2,000 records per batch, 12,000 characters per record, and 50 search results.

## Import format

Call `import_records` with normalized objects containing `id`, `role`, `content`, and optional `created_at`. Provider-specific export parsers should be separate adapters; do not pass raw archives or execute embedded code.

## Integration status

This is a storage/API foundation. CLI import commands, desktop consent UI, provider export parsers, retention scheduling, semantic/vector retrieval, and coordinator prompt-context integration remain follow-up work. The database path and authorization callback must be provided by the application composition root.
