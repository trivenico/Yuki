# Conversation Archival During Active Generation Audit

## Scope

Reviewed `core/conversation_store.py`, `core/conversation_service.py`, `core/conversation_application_runtime.py`, `api/runtime.py`, and available conversation tests at PR #1346 head `ea67820beda0bc0b1a393c19f393236e9e9a2adc`.

Source inspection only. No runtime code changed and no tests executed.

## Confirmed finding: archiving an active conversation can strand its generation in the running state

### Evidence

1. `ConversationStore.archive_conversation()` performs an independent transaction that sets `conversations.archived=1`. It does not check for a running generation, coordinate with the generation lease, cancel active work, or transition any generation record.
2. The active generation continues through `AgentExecutionLoop.run()`. When it attempts to persist its assistant message, `ConversationStore.append_message()` checks `conversation["archived"]` and raises `ConversationConflict("conversation is archived.")` before inserting the assistant response.
3. The generation's terminal transition occurs only after assistant-message persistence in `ConversationService._reply_unleased()`. Because the append raises first, `finish_generation()` is skipped.
4. The outer reply cleanup releases the generation lease, leaving the generation row with status `running`.
5. Subsequent recovery only occurs as part of another reply. Recovery tries to append an assistant interruption message, but the same archived-conversation guard rejects that write. The inspected store exposes archive but no corresponding unarchive or delete operation.
6. The generation row therefore remains durably `running` and cannot be reconciled through the normal reply path while the conversation remains archived.

### Impact

Archiving during an active model run can leave a permanently non-terminal generation record and discard the active run's assistant result from conversation history. This can distort generation status reporting and leaves recovery unable to complete through the ordinary service path.

This finding concerns archival racing with an in-flight generation. It does not imply that archiving starts a new generation or bypasses lease fencing.

### Recommended remediation

- Define archival semantics for active work: either reject archival while a generation is running, or atomically mark/cancel the generation and ensure the worker cannot later commit.
- If archival is allowed to proceed, make the worker's terminal path handle an archived-conversation conflict and transition the generation to a deliberate terminal state without attempting a prohibited message write.
- Provide an explicit recovery/administrative path for terminalizing orphaned running generations on archived conversations.
- Add a deterministic test with a blocked model run: archive the conversation while the run is blocked, release the model, and assert the documented result, generation terminal status, and lease cleanup.
- Add a recovery test proving archived conversations do not accumulate permanently running generation rows.

## Existing protections

- Archival is committed transactionally.
- Assistant writes are rejected for archived conversations.
- Assistant writes are fenced by the active lease token.
- Generation rows and messages have foreign-key relationships and cascade on conversation deletion (although no delete API was found in the reviewed store).

## Severity and confidence

- **Classification:** lifecycle consistency / archival concurrency.
- **Confidence:** high for the described control flow.
- **Trigger:** archive a conversation after its user message and running generation have been committed but before assistant persistence.
- **Production occurrence:** not established.

## Validation status

- Source inspection only.
- No tests executed.
- No runtime code changed.
