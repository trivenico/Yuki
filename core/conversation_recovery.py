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
            self.store.mark_started_tool_executions_uncertain(
                owner_id=owner_id, conversation_id=conversation_id,
                request_id=request_id, lease_token=lease_token,
            )
            conversation = self.store.get_conversation(
                owner_id=owner_id, conversation_id=conversation_id
            )
            user_message = next((item for item in self.store.list_messages(
                owner_id=owner_id, conversation_id=conversation_id, limit=200
            ) if item["message_id"] == generation["user_message_id"]), None)
            assistant_exists = self.store.has_message(
                owner_id=owner_id, conversation_id=conversation_id,
                idempotency_key="assistant:" + request_id,
            )
            if assistant_exists:
                self.store.finish_generation(
                    owner_id=owner_id, conversation_id=conversation_id,
                    request_id=request_id, status="completed", lease_token=lease_token,
                )
                completed += 1
            elif user_message is not None:
                self.store.append_message(
                    owner_id=owner_id, conversation_id=conversation_id,
                    role="assistant", content=self.INTERRUPTION_MESSAGE,
                    idempotency_key="assistant:" + request_id,
                    expected_version=conversation["version"], lease_token=lease_token,
                )
                self.store.finish_generation(
                    owner_id=owner_id, conversation_id=conversation_id,
                    request_id=request_id, status="interrupted", lease_token=lease_token,
                )
                interrupted += 1
            else:
                self.store.finish_generation(
                    owner_id=owner_id, conversation_id=conversation_id,
                    request_id=request_id, status="interrupted", lease_token=lease_token,
                )
                orphaned += 1
        return RecoverySummary(
            recovered_request_ids=tuple(recovered),
            completed_generations=completed,
            interrupted_generations=interrupted,
            orphaned_generations=orphaned,
            batch_limit_reached=len(pending) == self.BATCH_LIMIT,
        )
