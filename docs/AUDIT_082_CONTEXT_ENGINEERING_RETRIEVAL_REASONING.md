# Audit 082 — Context Engineering, Knowledge Retrieval, and Reasoning Reliability

**Repository:** `trivenico/Yuki`  
**Base reviewed:** `main`  
**Scope:** conversation transcript construction, history selection, agent execution context, tool-result handling, provider message validation, prompt guard, and retrieval integration.

## Executive summary

The reviewed execution path has several strong foundations: system guidance is placed in a system message; persisted history accepts only user/assistant roles; history and transcript sizes are bounded; tool results are explicitly labeled untrusted; tool dispatch remains behind the guarded protocol; and provider adapters independently validate message roles and total character limits.

The current design is bounded but not yet context-window-aware. Character counts are only a rough proxy for model tokens, history selection is a fixed message-count slice, and no shared provider-aware context planner or complete-turn compaction contract was identified in the reviewed path.

## Findings

### P1 — Context limits are character-based, not model-token-aware

`AgentExecutionLoop` limits transcript content to a configured character budget (default 80,000), while the inspected provider adapters independently impose a 100,000-character message limit. Character count does not reliably predict tokens across languages, code, JSON, or model tokenizers. The application may therefore send a transcript that passes local validation but exceeds the selected model's actual context window, or unnecessarily discard usable context.

**Recommendation:** add a provider/model context capability contract and a shared planner that budgets system instructions, tool schemas, history, current user input, and reserved output tokens using the selected model's tokenizer or conservative model-specific estimates. Keep a hard character/byte ceiling as a secondary safety bound.

### P1 — History slicing is message-count based, not turn-aware

`ConversationService._reply_unleased` selects the final 126 persisted messages using a simple slice. The execution loop then accepts those messages as history. The reviewed code does not establish a semantic compaction contract that guarantees the selected window begins at a complete user/assistant turn boundary or retains the most relevant facts from older turns.

A fixed message count can over-retain repetitive short exchanges, under-retain important context in long messages, and discard older constraints or decisions without a durable summary.

**Recommendation:** select complete conversation turns under the model-specific token budget. Preserve the system/developer policy separately, keep the latest user intent intact, and maintain provenance-linked summaries of older turns. Summaries must remain data, not elevated instructions.

### P2 — No explicit context relevance or freshness policy in the conversation path

The inspected conversation service builds context from recent persisted messages. The reviewed path does not show a unified policy for retrieving relevant long-term memories, resolving conflicting facts, aging stale preferences, or explaining why a memory was selected. The lexical memory implementation is separate and should not be assumed to participate in every conversation.

**Recommendation:** introduce an authorized retrieval stage with namespace filtering before ranking, provenance and trust labels, freshness/confidence metadata, conflict handling, and a bounded evidence section. Make retrieval optional and observable rather than silently injecting arbitrary memories.

### P2 — Tool-result trust labeling is textual and not a typed message boundary

The execution loop appends formatted tool results as a user-role message prefixed with a warning that results are untrusted evidence. This is a useful prompt-level defense, but the provider-neutral message contract represents only role and content; it does not carry a structured provenance/trust field. Downstream model adapters therefore rely on the textual wrapper being preserved.

**Recommendation:** define an internal typed context item with origin, trust class, source/tool identity, and content. Render it consistently per provider while preserving explicit untrusted framing. Do not let retrieved or tool-produced content alter system policy, tool permissions, or identity.

### P2 — Prompt-injection scanning is heuristic and must not be used as a context authorization decision

`core/prompt_guard.py` scans a bounded prefix with regular expressions and explicitly states that no match does not prove safety. It is a useful signal detector, not a classifier with reliable coverage and not an authorization boundary.

**Recommendation:** keep the scanner advisory; use provenance, namespace ACLs, typed trust boundaries, and tool authorization as the enforcement mechanisms. Add adversarial tests for encoded, multilingual, indirect, and instruction-like content, but do not treat a clean scan as trusted.

### P2 — Provider context constraints are duplicated

The OpenAI-compatible and Ollama adapters each validate message count, roles, and aggregate character size. The rules are similar but implemented separately, while actual model context-window metadata and output reservation are not part of the common adapter contract inspected here.

**Recommendation:** centralize provider-neutral structural validation and add provider-specific capability metadata. Keep adapter-side validation as defense in depth and test parity across providers.

## Existing positive controls

- System guidance is inserted as the first system message.
- Persisted history roles are restricted to user and assistant; callers cannot inject a system role through history.
- History is bounded to 126 prior messages and each message has a maximum content length.
- The agent loop enforces an aggregate transcript character ceiling before model turns.
- Tool results are labeled as untrusted evidence and explicitly not to be followed as instructions.
- Tool proposals are parsed and validated before guarded dispatch.
- OpenAI-compatible and Ollama adapters independently validate message shapes, roles, message counts, and aggregate character limits.
- The prompt guard explicitly documents its heuristic limitations.

## Recommended context architecture

1. **Context planner:** model-aware token accounting with reserved output budget.
2. **Instruction hierarchy:** immutable trusted policy/system guidance separated from user input, retrieved memories, and tool observations.
3. **Turn-aware history:** select complete turns; compact older turns into provenance-linked summaries.
4. **Evidence retrieval:** authorize namespace first, then rank by relevance, freshness, confidence, and source quality.
5. **Typed context provenance:** represent origin and trust class structurally, not only in prose wrappers.
6. **Conflict resolution:** retain source timestamps and confidence; do not silently overwrite contradictory user facts.
7. **Provider capabilities:** context window, tokenizer/estimator, output limits, and tool-schema overhead per model.
8. **Evaluation:** measure recall, instruction hierarchy adherence, hallucination, stale-memory use, and context-budget behavior.

## Required regression/adversarial tests

- A long multilingual/code-heavy transcript stays within the selected model's token budget.
- History compaction never splits a user/assistant turn and preserves the latest user request.
- Older critical constraints survive summarization with source provenance.
- Tool output containing prompt injection remains untrusted after every provider rendering path.
- Retrieved memory cannot override system guidance or grant tools.
- Provider adapters enforce equivalent message-shape rules and their own model-specific limits.
- Conflicting and stale memories are surfaced or resolved by an explicit policy rather than silently blended.
- Context planner reserves sufficient output tokens and handles oversized tool schemas.

## Validation limits

Source inspection only. No runtime code was changed and no tests were executed. No real model tokenizer/context-window integration, provider request, multilingual token-budget benchmark, long-conversation evaluation, or prompt-injection red-team suite was run. The findings describe the inspected paths and do not prove that no other subsystem provides complementary context management.
