# Yuki AI application service

Yuki AI is the shared intelligence service used by every interface. CLI and native desktop call the same application service rather than implementing separate prompting, planning or memory behavior.

## Current service surface

- `YukiAI.answer(message)`: provider-independent chat with bounded optional memory context.
- `YukiAI.plan(goal)`: bounded inspectable plan with optional memory context.
- `YukiAI.run_task(goal)`: delegates only when an approval-gated task agent was explicitly configured.
- `YukiAI.status()`: truthful runtime readiness and capability report.

Memory adapters are injected; retrieval and persistence failures are non-fatal. Retrieved memory is treated as untrusted reference data. The service never grants tool permissions. Existing skill execution and human approval gates remain authoritative.

## CLI and desktop

The CLI constructs this service from its configured provider and exposes `ai-status`, `plan`, `ask` and `chat`. Desktop imports the same `YukiAI` class through `interfaces.desktop.model_api`. Neither interface should duplicate provider routing or planner policy.

## Not claimed as complete

This is an application-level orchestration facade, not a newly trained foundation model. Its intelligence depends on the selected pretrained provider/model. Persistent cross-session memory adapters, production streaming, native desktop shell and model lifecycle transactions remain separate deliverables.


## Local memory and conversation history

- \`VectorMemory\` is the current local lexical-retrieval adapter backed by the bounded \`MemoryStore\`; it does not yet perform embedding-based semantic retrieval.
- CLI memory management is explicit: \`yuki memory add\`, \`search\`, \`list\`, and \`delete\`. Set \`YUKI_MEMORY_PATH\` to choose the local store location.
- Chat memory retrieval and automatic exchange persistence are disabled by default. Enable them deliberately with \`YUKI_ENABLE_MEMORY=1\`; this is a local opt-in, not a model-training switch.
- Conversation exports use the existing preview/redaction/consent importer. Imported content remains ineligible for training until separately reviewed and approved.
- The desktop facade exports the same memory and conversation-import services. Desktop must provide clear memory controls, import preview, redaction visibility, and deletion controls before enabling persistence.

## Hybrid memory retrieval

- `VectorMemory` supports dependency-free lexical retrieval by default.
- An explicitly injected `embedding_fn(text)` enables cosine-similarity retrieval blended with lexical overlap; no embedding model is downloaded or selected implicitly.
- Invalid vectors, dimension mismatches and embedding failures fall back to lexical matching.
- A bounded 512-entry in-process embedding cache avoids repeatedly embedding the same saved memories during a process lifetime. The first semantic search can still embed up to the bounded 200 most recent memories.
- To enable semantic retrieval in a deployment, inject an embedding function backed by a locally installed, revision-pinned embedding model and document its model/license. CLI currently uses lexical fallback unless a future configuration explicitly supplies that adapter.
- This change improves retrieval capability but does not fine-tune model weights or make a foundation model from scratch.
