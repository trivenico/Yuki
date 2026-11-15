"""Lease-fenced recovery of interrupted conversation generations."""
from __future__ import annotations

from dataclasses import dataclass

from core.conversation_store import ConversationConflict, ConversationStore


@dataclass(frozen=True)
class RecoverySummary:
    recovered_request_ids: tuple[str, ...]
    completed_generations: int
    interrupted_generations: int
    orphaned_generations: int
    batch_limit_reached: bool


class ConversationRecoveryCoordinator:
    """Recover one bounded page without replaying model or tool side effects."""

    BATCH_LIMIT = 100
    INTERRUPTION_MESSAGE = (
        "This response was interrupted before completion. Please submit a new request; "
        "Yuki will not automatically repeat potentially side-effecting actions."
    )

    def __init__(self, store: ConversationStore):
        if not isinstance(store, ConversationStore):
            raise TypeError("store must be a ConversationStore.")
        self.store = store

    def recover(
        self, *, owner_id: str, conversation_id: str, lease_token: str,
    ) -> RecoverySummary:
        pending = self.store.list_interrupted_generations(
            owner_id=owner_id, conversation_id=conversation_id, limit=self.BATCH_LIMIT
        )
        recovered: list[str] = []
        completed = interrupted = orphaned = 0
        for generation in pending:
            request_id = generation["request_id"]
            recovered.append(request_id)
            uncertain_count = self.store.mark_started_tool_executions_uncertain(
                owner_id=owner_id, conversation_id=conversation_id,
                request_id=request_id, lease_token=lease_token,
            )
            conversation = self.store.get_conversation(
                owner_id=owner_id, conversation_id=conversation_id
            )
            user_message_exists = self.store.has_message_id(
                owner_id=owner_id, conversation_id=conversation_id,
                message_id=generation["user_message_id"],
            )
            assistant_key = "assistant:" + request_id
            assistant_exists = self.store.has_message(
                owner_id=owner_id, conversation_id=conversation_id,
                idempotency_key=assistant_key,
            )
            # An interruption notice may have been persisted before the audit
            # transaction failed. On retry, preserve its interrupted outcome
            # instead of misclassifying the assistant message as a completed turn.
            assistant_message = (
                self.store.get_message_by_idempotency_key(
                    owner_id=owner_id, conversation_id=conversation_id,
                    idempotency_key=assistant_key,
                ) if assistant_exists else None
            )
            interruption_persisted = (
                assistant_message is not None
                and assistant_message.get("content") == self.INTERRUPTION_MESSAGE
            )
            if interruption_persisted:
                finalized = self.store.finish_generation_with_recovery_event(
                    owner_id=owner_id, conversation_id=conversation_id,
                    request_id=request_id, status="interrupted", lease_token=lease_token,
                    outcome="interrupted", uncertain_tool_calls=uncertain_count,
                )
                if not finalized:
                    raise ConversationConflict("interrupted generation changed during recovery.")
                interrupted += 1
            elif assistant_exists:
                finalized = self.store.finish_generation_with_recovery_event(
                    owner_id=owner_id, conversation_id=conversation_id,
                    request_id=request_id, status="completed", lease_token=lease_token,
                    outcome="completed", uncertain_tool_calls=uncertain_count,
                )
                if not finalized:
                    raise ConversationConflict("interrupted generation changed during recovery.")
                completed += 1
            elif user_message_exists:
                self.store.append_message(
                    owner_id=owner_id, conversation_id=conversation_id,
                    role="assistant", content=self.INTERRUPTION_MESSAGE,
                    idempotency_key="assistant:" + request_id,
                    expected_version=conversation["version"], lease_token=lease_token,
                )
                finalized = self.store.finish_generation_with_recovery_event(
                    owner_id=owner_id, conversation_id=conversation_id,
                    request_id=request_id, status="interrupted", lease_token=lease_token,
                    outcome="interrupted", uncertain_tool_calls=uncertain_count,
                )
                if not finalized:
                    raise ConversationConflict("interrupted generation changed during recovery.")
                interrupted += 1
            else:
                finalized = self.store.finish_generation_with_recovery_event(
                    owner_id=owner_id, conversation_id=conversation_id,
                    request_id=request_id, status="interrupted", lease_token=lease_token,
                    outcome="orphaned", uncertain_tool_calls=uncertain_count,
                )
                if not finalized:
                    raise ConversationConflict("interrupted generation changed during recovery.")
                orphaned += 1
        return RecoverySummary(
            recovered_request_ids=tuple(recovered),
            completed_generations=completed,
            interrupted_generations=interrupted,
            orphaned_generations=orphaned,
            batch_limit_reached=len(pending) == self.BATCH_LIMIT,
        )
