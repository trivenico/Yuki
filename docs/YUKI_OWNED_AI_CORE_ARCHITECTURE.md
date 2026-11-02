# Yuki-owned AI core architecture

## Product decision

Yuki is its own AI product and runtime, not a thin wrapper around one vendor. Its identity, task lifecycle, planning, memory, retrieval, permissions, skills, tools, agent coordination, evaluation, observability, and user interfaces are owned by this repository.

External models are optional inference engines. They can improve day-one capability and can be replaced without changing Yuki's memory format, agent contracts, tool policy, task database, CLI, or desktop application. A deployment with a self-hosted model or local inference should remain usable without an external AI account, subject to local hardware and model capability.

## Core components Yuki owns

1. **Identity and interaction:** persona, user/session context, conversation state and interface contracts.
2. **Intelligence orchestration:** classify task, assemble bounded context, retrieve approved memory, select a model route, validate the result and expose uncertainty.
3. **Planning:** convert goals into inspectable plans and dependency graphs. Planning proposals never grant tool permissions.
4. **Memory and knowledge:** user-controlled persistent memory, project knowledge, document indexing, retrieval, provenance, retention and deletion controls.
5. **Agent system:** typed specialist contracts, registry, delegation policy, parent/child lineage, durable task queues, scheduling, retries and cancellation.
6. **Skills and tools:** skill discovery, versioning, MCP adapters, schemas, allow-lists, approval records, execution sandboxes and audit logs.
7. **Verification:** tests, source checks, independent result validation, quality scoring and regression evaluation.
8. **Learning:** feedback capture, benchmark datasets, approved memory/skill updates, model evaluation and optional future fine-tuning.
9. **Operations:** provider health, model/agent observability, metrics, tracing, cost controls, backup/restore, secrets and deployment lifecycle.
10. **Clients:** CLI and desktop are clients of one shared local/cloud runtime, not separate intelligence implementations.

## Provider independence

Providers implement an inference interface. A provider may be:
- A local runtime such as Ollama or llama.cpp.
- A self-hosted inference server such as vLLM.
- A model hosted by Yuki on infrastructure Yuki operates.
- An optional external API explicitly configured by the user.

Yuki owns provider registration, capability declarations, routing policy, consent, timeouts, failure handling and evaluation. No external provider is required by the architecture, but a local/self-hosted model and adequate compute are required for a fully offline deployment.

## New implementation in this increment

`core/intelligence_engine.py` introduces a provider-neutral orchestration service that:
- Classifies requests into bounded task domains (coding, reasoning, research, general).
- Uses the existing router's `generate_for(task, prompt)` when available.
- Retrieves local memory only when enabled on the request.
- Marks user context and memory as untrusted reference data.
- Bounds input/context/output and reports provider failure without silent fallback.
- Returns task class, status and memory references for observability.

Tests cover task classification, model routing, memory retrieval/disablement, invalid requests and provider failures.

## Important boundary

This is the first orchestration seam, not a complete autonomous AI. It currently produces text answers and does not itself execute tools, browse, write files, or run code. Real-world actions must be planned through the existing planner and passed to the guarded skill/tool runtime, with fresh approvals, isolation and verification. Next integration work should connect this engine to the durable task runtime and CLI/desktop APIs.

## Build-versus-reuse policy

Build and own Yuki's core contracts, runtime, data model, policy and user experience. Reuse commodity infrastructure and upstream libraries where appropriate (tokenizers, inference servers, vector indexes, speech engines, browser automation, MCP SDKs), pinned and license-reviewed. Never outsource Yuki's core policy or user data ownership to an external agent platform.

## Independence acceptance gates

- Start and complete basic conversations with a local/self-hosted model and no external provider credentials.
- Switch or remove an inference provider without losing memory, tasks, skills or agent identity.
- Run the same task/evaluation suite across local and optional cloud providers.
- Keep secrets and private context out of client apps and unauthorized providers.
- Inspect, export and delete user-controlled memory and project data.
- Run core tests and guarded local tools without internet access.
- Rebuild CLI and desktop clients against the same runtime API.
