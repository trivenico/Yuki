# Audit 067 — Prompt Injection, Untrusted Input & Data Exfiltration

**Repository:** `trivenico/Yuki`  
**Base reviewed:** `main`  
**Audit date:** 2026-11-24  
**Scope:** Prompt-injection boundaries across user input, conversation history, indexed knowledge, local memory, model tool proposals, tool outputs, and MCP integrations.

## Executive summary

The inspected execution path contains meaningful safeguards: structured tool proposals are parsed before dispatch; the agent loop constrains tool names to the request's advertised tool set (per the current implementation); tool output serialization labels results as `untrusted_tool_output`; the loop explicitly tells the model to treat tool results as untrusted evidence; and MCP configuration/policy is host-owned rather than derived from remote tool metadata.

The remaining concern is not the absence of all defenses. It is that trust/provenance handling is uneven across ingestion and retrieval paths, while the prompt scanner is only a heuristic utility and should not be treated as an authorization or isolation control.

This audit records design gaps visible in the reviewed files. It does not claim a demonstrated exploit, a complete repository-wide call-graph review, or that all possible retrieval consumers were identified.

## Reviewed source

- `core/prompt_guard.py`
- `core/agent.py`
- `core/agent_execution_loop.py`
- `core/structured_tool_protocol.py`
- `core/guarded_tool_runtime.py`
- `core/vector_memory.py`
- `companion/memory.py`
- `knowledge/indexer.py`
- `core/mcp_runtime.py`
- `tests/test_agent_execution_loop.py`
- `tests/test_structured_tool_protocol.py`
- `tests/test_guarded_tool_runtime.py`
- `tests/test_mcp_runtime.py`

Some anticipated test paths (including `tests/test_prompt_guard.py`, `tests/test_vector_memory.py`, and `tests/test_knowledge_indexer.py`) were not present at those exact paths on the reviewed branch.

## Findings

### P1 — Knowledge ingestion does not preserve an explicit untrusted-source marker

**Evidence:** `knowledge/indexer.py::KnowledgeIndexer.index_file` reads workspace documents, chunks their text, and stores each chunk through `VectorMemory.remember` as a lesson. The stored text adds a `Source: <filename>` prefix, but the indexed content itself is not wrapped or tagged with an explicit trust classification or structured source metadata at this boundary.

**Risk:** Documents may contain adversarial instructions, fabricated role labels, requests to reveal secrets, or instructions to invoke tools. If a later consumer places retrieved memory into model context without preserving and enforcing its untrusted provenance, those instructions can be mistaken for operational guidance. The inspected files do not establish every downstream consumer, so this is a boundary gap, not a claim that a specific retrieval route is exploitable.

**Recommendation:**
- Store provenance as structured metadata (source ID, canonical workspace-relative path, content hash, ingestion time, trust class).
- Treat all imported document text as untrusted data regardless of file type or workspace location.
- Ensure retrieval APIs preserve that metadata and every prompt adapter places retrieved text in a clearly delimited data region.
- Keep authorization and tool permission decisions independent of retrieved content.
- Add regression tests with documents containing fake system/developer messages, tool instructions, and secret-exfiltration requests.

### P1 — Prompt-injection scanner is advisory and not an enforcement boundary

**Evidence:** `core/prompt_guard.py` describes itself as a heuristic scanner and explicitly says it is not a substitute for isolation or policy enforcement. `scan_untrusted_text` matches a small set of regular expressions, truncates the scan to a bounded prefix, and returns a recommendation. A no-match result explicitly does not prove safety.

**Risk:** Pattern matching cannot reliably detect paraphrases, multilingual or encoded attacks, indirect instructions, or benign-looking text that becomes dangerous in context. A caller that treats `flagged=False` as a safety verdict would be vulnerable to false negatives. The reviewed agent execution and knowledge-indexing files do not show this scanner being used as a mandatory gate.

**Recommendation:**
- Keep the scanner as telemetry/triage only; never use it as permission to trust content.
- Apply source-based trust labels and policy enforcement regardless of scanner output.
- Add tests for truncation, Unicode, obfuscated instructions, multilingual variants, and false-positive behavior.
- If the scanner is intended to be part of a production pipeline, explicitly document and test its call sites; do not imply it is globally enforced.

### P1 — Tool-output trust is explicit in the structured protocol, but this guarantee is adapter-specific

**Evidence:** `StructuredToolProtocol.format_results` serializes each output with `trust: untrusted_tool_output`. `AgentExecutionLoop.run` also prefixes the serialized result with an instruction to treat it as untrusted evidence.

**Risk:** This is a useful defense in the inspected adapter, but a JSON field and a natural-language instruction are not a security boundary on their own. Any alternate provider adapter, orchestration path, logging/replay path, or future prompt builder that omits the trust handling could reintroduce indirect prompt injection.

**Recommendation:**
- Centralize prompt-message construction for untrusted tool output and retrieved content.
- Preserve provenance and trust labels across provider-neutral message types, not only serialized text.
- Keep tool authorization, argument validation, and approval checks outside model interpretation.
- Add integration tests that return hostile tool output and verify it cannot bypass the guarded execution boundary on subsequent turns.

### P2 — Memory records have categories but no first-class trust/provenance schema

**Evidence:** `companion/memory.py::MemoryStore.add` persists `id`, `created_at`, `category`, and `text`. `core/vector_memory.py` searches these records lexically and returns the stored item with a score. The visible schema has no source identity, content hash, or trust classification.

**Risk:** A category such as `lesson` describes the kind of memory, not whether its text is trusted. This makes it difficult for consumers to distinguish user-authored preferences from imported documents or tool-derived content if those sources are all stored through the same API.

**Recommendation:**
- Add a backward-compatible provenance/trust metadata model.
- Define separate write paths for user-confirmed memory and imported/untrusted evidence.
- Make retrieval consumers handle untrusted content as data and never as policy.
- Include migration and compatibility tests for existing JSON memory files.

### P2 — Workspace containment check should be hardened against filesystem races

**Evidence:** `KnowledgeIndexer.index_file` resolves a candidate path, checks workspace containment, then separately stats and reads the resolved path. It also checks `resolved.is_symlink()` after resolving the path; because resolution follows symlinks, that check does not reliably identify that the original candidate was a symlink.

**Risk:** The post-resolution symlink check is ineffective as a symlink detector, and path validation followed by a separate read can be vulnerable to filesystem changes between those operations in environments where an attacker can modify the workspace concurrently. The workspace trust model and attacker write access were not established in this review.

**Recommendation:**
- Decide explicitly whether in-workspace symlinks are allowed; enforce that policy on the original path components.
- Use descriptor-based, no-follow file opening and verify the opened file's resolved identity/containment before reading where supported.
- Avoid check-then-open path races; add tests for symlink escapes and replacement races.
- Keep file size and supported-type limits enforced on the opened file.

## Existing controls worth preserving

- Structured tool envelopes reject malformed shapes, duplicate IDs, oversized arguments, and malformed later calls before dispatching any call in the batch.
- The agent execution loop has bounded turns, tool calls, duration, and transcript size, and checks cooperative cancellation before dispatch.
- Tool results are marked as untrusted evidence in the structured protocol and in the inspected agent loop.
- The inspected MCP runtime uses trusted host configuration, owner-scoped policy, required OAuth scopes, and revision fencing; provider-supplied tool metadata is not itself authorization.
- The basic agent skill path requests explicit approval for each invocation.

## Remediation priorities

1. Establish a shared provenance/trust contract for every source that can enter model context: user input, conversation history, memory, knowledge files, tool output, and MCP responses.
2. Preserve trust metadata through ingestion, retrieval, prompt assembly, and replay.
3. Keep authorization and data-access filtering outside model prompts; enforce them before retrieval and again at side-effect boundaries.
4. Add adversarial integration tests across the full model/tool/retrieval loop.
5. Harden knowledge-file opening against symlink and time-of-check/time-of-use races.
6. Treat prompt scanning as defense-in-depth telemetry only.

## Verification status

- Source review: completed for the paths listed above.
- Repository-wide call-graph coverage: not established.
- Regression tests added in this audit: none; no narrow implementation change was made because the primary gaps require a consistent cross-path provenance contract rather than a local regex gate.
- Tests executed: none.
- PR merge/deployment: not performed.

## Follow-up

Next recommended audit: **068 — Secrets Management, Credential Lifecycle & Redaction**, including secret storage, provider credentials, logs, traces, memory, exports, and rotation/revocation behavior.
