# Yuki Local Knowledge Engine

The local knowledge engine stores reference documents independently from personal memory.

## Storage boundary

- Personal memory remains in the consent-controlled memory service.
- Knowledge sources and chunks live in a separate SQLite database (default: `YukiData/knowledge.sqlite3`).
- Model weights and training datasets are separate resources; this engine does not modify either.
- The default `YukiData/` directory is excluded from Git. Never commit private documents or local indexes.

## Supported sources

The low-level `KnowledgeEngine.ingest_file()` API accepts explicitly selected UTF-8 TXT, Markdown, RST, Python, JSON, YAML and YML files.

The `DocumentParser` and `SemanticIndexCoordinator` pipeline additionally supports HTML, DOCX and PDF. PDF extraction requires the optional `requirements-documents.txt` dependency. Scanned PDFs do not yet receive OCR. HTML and DOCX parsing use local standard-library parsers.

No directory crawling, remote URL downloading, source-code execution or automatic web ingestion is enabled.

## Example: lexical ingestion

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

## Example: document and semantic ingestion

```python
from knowledge import (
    KnowledgeEngine, VectorStore, SentenceTransformerEmbedder,
    SemanticIndexCoordinator,
)

engine = KnowledgeEngine()
vectors = VectorStore("YukiData/knowledge-vectors.sqlite3")
embedder = SentenceTransformerEmbedder.from_local_cache(
    "sentence-transformers/all-MiniLM-L6-v2",
)
coordinator = SemanticIndexCoordinator(engine, vectors, embedder)
result = coordinator.index_file("research/paper.pdf", workspace=".")
```

Cache the selected Hugging Face model explicitly before runtime. Runtime model loading is local-only and does not silently download weights.

## Safety and limits

- A caller must explicitly select a file and approved workspace.
- Files outside the workspace and symbolic links are rejected.
- Supported extensions are allowlisted.
- The lexical engine limits input files to 2 MB; the document parser limits inputs to 20 MB.
- The document parser limits DOCX expanded archive size, archive entry count, PDF page count and extracted text.
- Extracted document text is capped at 200,000 characters.
- Text is split into overlapping chunks of at most 2,000 characters.
- SQLite parameter binding is used for all values; search terms are tokenized and quoted before FTS5 matching.
- Search returns source IDs, source names, source paths, chunk IDs and relevance scores for provenance.
- Unchanged source content is detected by SHA-256 and is not re-indexed.
- Re-indexing changed content replaces its prior chunks transactionally.
- Embeddings are generated before canonical replacement, so parsing or embedding failure preserves the previous source index.
- Lexical and vector databases are separate SQLite files; a crash or vector-store failure after canonical replacement can require re-indexing to reconcile.
- Source deletion removes its chunks and full-text index rows; the semantic coordinator also deletes vectors for its configured embedding model.
- SQLite FTS5 is required. The core lexical engine needs no embedding model, external AI API, network call or training operation.

## Current limitations

This is a local retrieval foundation, not a complete RAG pipeline. OCR, web crawling, claim verification, freshness evaluation, automatic knowledge-graph extraction and training data curation remain future work. Semantic vector search currently performs an exact local scan and should be benchmarked before scaling to very large corpora.

Retrieved content is untrusted evidence. It must never authorize tools, override system policy or execute instructions embedded in documents.

## Evidence-backed claims

`EvidenceLedger` stores proposed claims separately from source documents and personal memory. Every evidence item must resolve to a current canonical knowledge chunk; caller-supplied text alone cannot be attached as evidence.

```python
from knowledge import KnowledgeEngine, EvidenceLedger

engine = KnowledgeEngine()
ledger = EvidenceLedger(engine)
claim = ledger.create_claim(
    "Yuki uses local semantic retrieval.",
    [canonical_chunk_id],
    stance="supports",
)
```

Claim states are `proposed`, `supported`, `contested`, `verified` and `rejected`. Creating a claim never verifies it. Verification is an explicit review action, requires supporting evidence from at least two distinct source IDs, and is blocked when refuting evidence exists. Each review records reviewer, method, rationale and the source IDs considered. A reviewed claim must be explicitly reopened before its evidence can be changed.

The two-source rule is a conservative workflow gate, not proof of source independence, truth, or source quality. Distinct source IDs can still represent copied material. Reviewer identity and review method are supplied by the trusted caller; this module does not authenticate reviewers or establish cryptographic attestation. Do not expose review operations directly to untrusted model output.

Evidence records retain canonical chunk and source IDs, not a permanent copy of the source text. If a source is deleted or re-indexed, evidence may become stale; resolve chunk IDs against the knowledge engine before presenting evidence, and re-evaluate affected claims. Claim extraction, source credibility scoring, contradiction resolution and graph synchronization remain separate future components.

## Verified knowledge graph synchronization

The existing `KnowledgeGraph` remains the graph storage implementation. `VerifiedKnowledgeGraphSync` is an explicit projection adapter; it does not perform model extraction or approve claims.

```python
from knowledge import (
    EvidenceLedger, KnowledgeGraph, KnowledgeEngine,
    VerifiedKnowledgeGraphSync,
)

engine = KnowledgeEngine()
ledger = EvidenceLedger(engine)
graph = KnowledgeGraph("YukiData/knowledge-graph.json")
sync = VerifiedKnowledgeGraphSync(ledger, graph)

result = sync.sync_claim(
    verified_claim_id,
    entities=[
        {"name": "Yuki", "kind": "project",
         "description": "Local AI assistant and agent platform."},
        {"name": "Knowledge Engine", "kind": "concept",
         "description": "Source-grounded local knowledge subsystem."},
    ],
    relations=[
        {"source": "Yuki", "type": "uses", "target": "Knowledge Engine"}
    ],
)
```

The adapter requires an explicitly verified claim, a recorded verification review, at least two distinct current supporting source IDs and no stale evidence. The caller supplies the proposed graph entities and relations; this module does not trust arbitrary model-generated graph mutations. Graph endpoints must be present in the supplied entity set and graph types are allowlisted.

Synchronization is idempotent for the same claim, evidence and graph projection. A persisted plan hash rejects silent projection drift on retry. Graph JSON and the sync audit SQLite database are separate stores, so a process crash can leave graph writes without an audit record; graph entity/relation insertion is idempotent and retry can complete the audit record. This is not a cross-store atomic transaction. Sync records retain evidence IDs for traceability; consumers must re-read the claim before presenting graph facts as current. Reopening or rejecting a claim prevents new sync operations but does not automatically retract an existing graph projection. Retraction and reconciliation are explicit future work.

Only trusted application code should call this adapter after reviewer authorization. Reviewer authentication, graph write authorization, provenance-aware graph reads and revocation are not provided by this module.

## Bounded autonomous research planning

`ResearchPlanner` creates durable, inspectable research plans in its own SQLite database. It defines seven ordered stages: scope, local retrieval, gap analysis, authorized source discovery, evidence analysis, synthesis and report generation.

```python
from research import ResearchPlanner

planner = ResearchPlanner("YukiData/research-plans.sqlite3")
plan = planner.create_plan(
    "Evaluate local-first AI assistant architectures.",
    questions=[
        "Which local inference runtimes are supported?",
        "How is evidence-backed memory maintained?",
    ],
)
```

Plan creation is proposal-only. It does not call a model, access the network, execute tools, ingest documents, or mutate the knowledge graph. The plan explicitly labels network reads, knowledge writes and report writes as side-effect boundaries.

An optional configured `TaskStore` allows `enqueue_for_approval(plan_id)` to submit a `research.investigate` task with an idempotency key, three retry attempts, no application-level token quota (`budget_tokens=None`), no wall-clock quota (`budget_seconds=None`), and `approval_required=True`. Yuki is designed as a personal-use system without metered usage caps. Model context windows, available RAM/VRAM, storage, throughput and hardware remain physical constraints; the runtime should manage these through context compaction, streaming, checkpointing, model selection and resource-aware scheduling rather than imposing artificial token quotas. Queue submission is not execution. A worker must independently authenticate the actor, enforce connector/source policy, validate every step and tool, and require review before evidence or graph writes. The plan/task stores are separate databases; idempotency makes retry safe after partial failure, but does not provide a cross-database transaction. Cancelling a queued plan must use the authorized TaskStore cancellation path.

The planner intentionally does not contain model-specific prompt code. Local model orchestration, worker implementation, source connector authorization, checkpointed step execution and report publication are separate integration work.


## Local-first provider default

Yuki's default provider is now Ollama. `config.yaml` selects `provider: ollama` and a configurable starter model (`qwen3:8b`); the factory also defaults to Ollama when no provider key is supplied. Cloud providers remain available only through explicit provider selection or an explicitly configured hybrid task allow-list. There is no implicit cloud fallback.

The configured model is not automatically downloaded by provider construction. First-run setup must inspect host hardware, check whether Ollama is installed and reachable, estimate a compatible model size, present the model/license/storage choice, and obtain user approval before downloading. The current default model may be too large for some computers; users should choose a smaller quantization/model when RAM or VRAM is limited. Model selection must be validated against the actual machine, not assumed from this config value.

This is a personal-use, unmetered application policy: no application-level message/token quota is imposed. Physical limits still apply (model context window, RAM/VRAM, disk, throughput, thermal envelope and OS process limits). Runtime resource management should prefer streaming, checkpointing, context compaction and graceful backpressure, not artificial usage metering. Cloud API costs, if a user explicitly enables a cloud provider, remain controlled by that external provider.

## Local research retrieval worker

`LocalResearchWorker` executes the local retrieval stage against the canonical `KnowledgeEngine` lexical index. It accepts an existing research plan, searches each question, de-duplicates chunks, preserves canonical source and chunk provenance, records unanswered questions, and persists an idempotent run record.

```python
from research import ResearchPlanner, LocalResearchWorker
from knowledge import KnowledgeEngine

planner = ResearchPlanner()
knowledge = KnowledgeEngine()
worker = LocalResearchWorker(planner, knowledge)
plan = planner.create_plan(
    "Investigate Yuki's memory architecture.",
    questions=["How is local memory stored?", "How is evidence reviewed?"],
)
result = worker.run_local_retrieval(plan["plan_id"])
```

This first worker is deliberately read-only: no model inference, external network access, file ingestion, evidence-ledger writes, graph writes or report publication. It is a local lexical retrieval stage, not yet an autonomous reasoning agent. Results are untrusted source material and must never be treated as instructions. A no-match result means only that the configured local index returned no match.

Retrieval is bounded to eight questions, five results per question and forty unique chunks per run. Runs are persisted separately from plans and keyed by a deterministic plan/query hash. Changes to the local knowledge index do not automatically invalidate an existing identical plan run; rerun freshness/invalidation policy is follow-up work. The worker is not yet wired into the TaskStore executor and has no actor authorization boundary of its own. It should only be called from trusted application code.


## Local model manager

`providers.model_manager` provides a dependency-light hardware inspection and local Ollama inventory API. `detect_hardware()` reports host RAM, available RAM, free workspace disk, CPU count and (when `nvidia-smi` is available) NVIDIA GPU/VRAM information. `recommend_models()` returns conservative starter suggestions based on available host RAM; VRAM is informational and does not override low host RAM. Estimates are not guarantees and must be followed by a real load/latency benchmark.

`OllamaLocalClient` accepts loopback HTTP endpoints only and reads `/api/tags`. It does not start Ollama, download models, or make remote requests. `LocalModelManager.inspect()` combines hardware, inventory and recommendations and explicitly reports that downloads were not performed. Model downloads, license review, checksum verification, disk-space reservation, installation rollback and first-run consent are separate future work. The module has no mandatory psutil dependency; when absent it attempts Linux `/proc/meminfo` detection.


## Unified local model manager and explicit Hub discovery

`LocalModelManager.discover_candidates()` is the explicit integration point between local hardware inspection and Hugging Face metadata discovery. It returns Hub candidate metadata alongside the current hardware profile and repeats the no-download/approval-required policy. Calling this method makes a bounded HTTPS metadata request to the official Hugging Face Hub; constructing or inspecting a local manager does not. The method does not calculate hardware compatibility from parameter counts because weight format, quantization, runtime overhead, context length and GPU offload are not yet known. Do not present candidates as compatible until a later model-specific estimator and actual benchmark validate them.
