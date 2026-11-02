"""Application service joining persisted conversations to the guarded agent loop.

The service accepts an authenticated owner_id from its caller. It never derives
identity from request payloads and never constructs an unguarded tool runtime.
"""
from __future__ import annotations

import threading
import uuid
from typing import Any, Sequence

from core.agent_execution_loop import AgentExecutionLoop
from core.conversation_store import ConversationConflict, ConversationStore


class ConversationService:
    """Synchronous conversation orchestration for a single application worker.

    A process-local lock avoids duplicate work inside one instance; a database
    lease additionally coordinates independent service instances sharing storage.
    """

    def __init__(self, store: ConversationStore, agent_loop: AgentExecutionLoop):
        if not isinstance(store, ConversationStore):
            raise TypeError("store must be a ConversationStore.")
        if not isinstance(agent_loop, AgentExecutionLoop):
            raise TypeError("agent_loop must be an AgentExecutionLoop.")
        self.store = store
        self.agent_loop = agent_loop
        self._lock = threading.RLock()

    def create(self, *, owner_id: str, title: str = "New conversation") -> dict[str, Any]:
        return self.store.create_conversation(owner_id=owner_id, title=title)

    def list(self, *, owner_id: str, limit: int = 50) -> list[dict[str, Any]]:
        return self.store.list_conversations(owner_id=owner_id, limit=limit)

    def history(
        self, *, owner_id: str, conversation_id: str, limit: int = 100,
        after_sequence: int | None = None,
    ) -> list[dict[str, Any]]:
        return self.store.list_messages(
            owner_id=owner_id, conversation_id=conversation_id,
            limit=limit, after_sequence=after_sequence,
        )

    def reply(self, *, owner_id: str, conversation_id: str, user_text: str, request_id: str, expected_version: int, tools: Sequence[dict[str, Any]], system_text: str | None = None) -> dict[str, Any]:
        token = str(uuid.uuid4())
        self.store.acquire_generation_lease(owner_id=owner_id, conversation_id=conversation_id, lease_token=token, ttl_seconds=600)
        try:
            return self._reply_unleased(owner_id=owner_id, conversation_id=conversation_id, user_text=user_text, request_id=request_id, expected_version=expected_version, tools=tools, system_text=system_text)
        finally:
            self.store.release_generation_lease(owner_id=owner_id, conversation_id=conversation_id, lease_token=token)

    def _reply_unleased(
        self, *, owner_id: str, conversation_id: str, user_text: str,
        request_id: str, expected_version: int,
        tools: Sequence[dict[str, Any]], system_text: str | None = None,
    ) -> dict[str, Any]:
        if not isinstance(request_id, str) or not request_id.strip() or len(request_id) > 160:
            raise ValueError("request_id must be non-empty text of at most 160 characters.")
        if type(expected_version) is not int or expected_version < 0:
            raise ValueError("expected_version must be a non-negative integer.")
        if not isinstance(user_text, str) or not user_text.strip() or len(user_text) > 20_000:
            raise ValueError("user_text must be non-empty text of at most 20000 characters.")
        if not isinstance(tools, (list, tuple)) or len(tools) > 32:
            raise ValueError("tools must contain at most 32 definitions.")

        with self._lock:
            # Check ownership and stale versions before invoking a model.
            conversation = self.store.get_conversation(
                owner_id=owner_id, conversation_id=conversation_id
            )
            existing_messages = self.store.list_messages(
                owner_id=owner_id, conversation_id=conversation_id, limit=200
            )
            if conversation["version"] != expected_version:
                # A retry with the same request ID is allowed to retrieve its
                # already persisted answer; all other stale writes fail closed.
                duplicate = self.store.append_message(
                    owner_id=owner_id, conversation_id=conversation_id,
                    role="user", content=user_text, idempotency_key=request_id,
                    expected_version=expected_version,
                )
                if duplicate["created"]:
                    raise ConversationConflict("conversation version changed.")
                return self._replay_or_conflict(
                    owner_id=owner_id, conversation_id=conversation_id,
                    user_message=duplicate["message"],
                )

            if existing_messages and existing_messages[-1]["role"] == "user":
                raise ConversationConflict(
                    "the previous turn has no persisted assistant response; reconcile it before sending another."
                )

            user_result = self.store.append_message(
                owner_id=owner_id, conversation_id=conversation_id,
                role="user", content=user_text, idempotency_key=request_id,
                expected_version=expected_version,
            )
            if not user_result["created"]:
                return self._replay_or_conflict(
                    owner_id=owner_id, conversation_id=conversation_id,
                    user_message=user_result["message"],
                )

            history = [
                {"role": item["role"], "content": item["content"]}
                for item in existing_messages[-126:]
            ]
            status, answer, model_turns, tool_calls = "completed", "", 0, 0
            try:
                kwargs = {"tools": list(tools), "history": history}
                if system_text is not None:
                    kwargs["system_text"] = system_text
                result = self.agent_loop.run(user_text, **kwargs)
                status = result.status
                model_turns = result.model_turns
                tool_calls = result.tool_calls
                if result.status == "completed":
                    answer = result.text
                else:
                    answer = (
                        "I couldn't complete that request within the current execution limits. "
                        "Please try a smaller or more specific request."
                    )
            except Exception:
                status = "failed"
                answer = "I couldn't complete that request because the AI service or a guarded operation failed."

            assistant_result = self.store.append_message(
                owner_id=owner_id, conversation_id=conversation_id,
                role="assistant", content=answer,
                idempotency_key=f"assistant:{request_id}",
                expected_version=user_result["version"],
            )
            return {
                "conversation_id": conversation_id,
                "status": status,
                "answer": assistant_result["message"]["content"],
                "version": assistant_result["version"],
                "model_turns": model_turns,
                "tool_calls": tool_calls,
                "replayed": False,
            }

    def _replay_or_conflict(self, *, owner_id: str, conversation_id: str, user_message: dict):
        following = self.store.list_messages(
            owner_id=owner_id, conversation_id=conversation_id,
            after_sequence=user_message["sequence"], limit=1,
        )
        if following and following[0]["role"] == "assistant":
            return {
                "conversation_id": conversation_id,
                "status": "replayed",
                "answer": following[0]["content"],
                "version": self.store.get_conversation(
                    owner_id=owner_id, conversation_id=conversation_id
                )["version"],
                "model_turns": 0,
                "tool_calls": 0,
                "replayed": True,
            }
        raise ConversationConflict(
            "this request was already recorded but has no completed assistant response."
        )
