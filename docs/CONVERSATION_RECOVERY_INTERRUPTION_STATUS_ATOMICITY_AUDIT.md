# Conversation Recovery: Interruption Message and Run Status Atomicity Audit

## Scope

Reviewed `core/conversation_service.py`, `core/conversation_store.py`, and `tests/test_conversation_service.py` at PR #1346 head `5c20f718c5cfe6a2f0715b46abef1949add4278c`.

Source inspection only. No runtime code changed and no tests executed.

## Confirmed finding: crash between recovery message append and status update misclassifies an interrupted run as completed

### Evidence

1. `ConversationService._reply_unleased()` reads durable generation records whose status is `running` and reconciles them before processing the incoming request.
2. If the corresponding user message has no following assistant message, recovery calls `store.append_message()` to append a synthetic assistant response stating that the previous response was interrupted and will not be replayed.
3. Only after that append returns does recovery call `store.finish_generation(..., status="interrupted")`.
4. These are separate store operations/transactions. There is no transaction spanning the assistant-message insert and generation-status update.
5. On a process crash after the assistant message commits but before `finish_generation()` commits, the generation row remains `running`.
6. On the next recovery pass, the code sees an assistant message immediately following the user message and unconditionally calls `finish_generation(..., status="completed")`.
7. The existing recovery test covers the normal completed-answer case, and the interrupted-recovery tests cover the successful two-step recovery path. The inspected tests do not cover a crash between the two recovery writes.

### Impact

The user-facing message remains the interruption notice, and the previous model/tool sequence is not replayed. However, the durable generation lifecycle is recorded as `completed` even though the actual generation was interrupted.

This can corrupt operational metrics, recovery reporting, audit trails, and any downstream logic that interprets terminal generation status. It is a status-integrity defect, not evidence that the side-effecting operation is replayed or that an incorrect assistant answer is exposed.

### Recommended remediation

- Make appending the recovery assistant message and transitioning the generation row to `interrupted` one atomic store transaction.
- Alternatively, persist an explicit recovery marker/type with the assistant message and have subsequent recovery distinguish a synthetic interruption message from a completed model answer. Do not infer completion from role alone.
- Make terminal transition outcomes explicit: check the boolean returned by `finish_generation()` and handle unexpected no-op transitions.
- Add a fault-injection test that raises/crashes after the interruption assistant insert but before the status update, then invokes recovery again and asserts the run remains `interrupted`.
- Retain the existing test that verifies a genuinely persisted assistant answer is reconciled as `completed`.

## Existing protections

- Recovery does not automatically replay an interrupted model/tool sequence.
- User and assistant messages use idempotency keys.
- Assistant persistence during active generation is fenced by a live matching lease token.
- Generation terminal updates are conditional on the row still being `running`.

## Severity and confidence

- **Classification:** durable state consistency / recovery lifecycle integrity.
- **Confidence:** high; the two writes are visibly separate and the second recovery branch infers completion solely from the presence of an assistant message.
- **Impact:** generation status can be falsely recorded as completed after a narrow crash window.
- **Production occurrence:** not established.

## Validation status

- Source inspection only.
- No tests executed.
- No runtime code changed.
