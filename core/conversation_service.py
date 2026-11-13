"""Application service joining persisted conversations to the guarded agent loop.

The service accepts an authenticated owner_id from its caller. It never derives
identity from request payloads and never constructs an unguarded tool runtime.
"""
from __future__ import annotations

import threading
import uuid
import weakref
from contextlib import contextmanager
from typing import Any, Callable, Sequence

from core.agent_execution_loop import AgentExecutionLoop
from core.conversation_store import ConversationConflict, ConversationStore


class ConversationService:
    """Synchronous conversation orchestration for a single application worker.

    A process-local lock avoids duplicate work inside one instance; a database
    lease additionally coordinates independent service instances sharing storage.
    """

    def __init__(self, store: ConversationStore, agent_loop: AgentExecutionLoop, *,
                 agent_loop_for_owner: Callable[[str], AgentExecutionLoop] | None = None):
        if not isinstance(store, ConversationStore):
            raise TypeError("store must be a ConversationStore.")
        if not isinstance(agent_loop, AgentExecutionLoop):
            raise TypeError("agent_loop must be an AgentExecutionLoop.")
        if agent_loop_for_owner is not None and not callable(agent_loop_for_owner):
            raise TypeError("agent_loop_for_owner must be callable.")
        self.store = store
        self.agent_loop = agent_loop
        self.agent_loop_for_owner = agent_loop_for_owner
        self._loop_owners = weakref.WeakKeyDictionary()
        self._loop_owners_guard = threading.Lock()
        self._lock_registry_guard = threading.Lock()
        self._conversation_locks = {}

    @contextmanager
    def _conversation_lock(self, key):
        """Serialize turns for one owner/conversation without blocking others."""
        with self._lock_registry_guard:
            entry = self._conversation_locks.get(key)
            if entry is None:
                entry = [threading.RLock(), 0]
                self._conversation_locks[key] = entry
            entry[1] += 1
        lock = entry[0]
        acquired = False
        try:
            lock.acquire()
            acquired = True
            yield
        finally:
            if acquired:
                lock.release()
            with self._lock_registry_guard:
                entry[1] -= 1
                if entry[1] == 0 and self._conversation_locks.get(key) is entry:
                    del self._conversation_locks[key]

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

    def reply(self, *, owner_id: str, conversation_id: str, user_text: str, request_id: str, expected_version: int, tools: Sequence[dict[str, Any]], system_text: str | None = None, on_text_delta=None, cancellation_requested=None) -> dict[str, Any]:
        token = str(uuid.uuid4())
        self.store.acquire_generation_lease(owner_id=owner_id, conversation_id=conversation_id, lease_token=token, ttl_seconds=3600)
        stop_heartbeat = threading.Event()
        lease_lost = threading.Event()

        def renew_lease():
            while not stop_heartbeat.wait(60):
                try:
                    renewed = self.store.renew_generation_lease(
                        owner_id=owner_id, conversation_id=conversation_id,
                        lease_token=token, ttl_seconds=3600,
                    )
                except Exception:
                    renewed = False
                if not renewed:
                    lease_lost.set()
                    return

        heartbeat = threading.Thread(
            target=renew_lease, name="yuki-generation-lease", daemon=True,
        )
        heartbeat.start()

        def should_cancel():
            if lease_lost.is_set():
                return True
            return bool(cancellation_requested and cancellation_requested())

        try:
            return self._reply_unleased(
                owner_id=owner_id, conversation_id=conversation_id,
                user_text=user_text, request_id=request_id,
                expected_version=expected_version, tools=tools,
                system_text=system_text, on_text_delta=on_text_delta,
                cancellation_requested=should_cancel, lease_token=token,
            )
        finally:
            stop_heartbeat.set()
            heartbeat.join(timeout=2)
            self.store.release_generation_lease(
                owner_id=owner_id, conversation_id=conversation_id, lease_token=token,
            )

    def _reply_unleased(
        self, *, owner_id: str, conversation_id: str, user_text: str,
        request_id: str, expected_version: int,
        tools: Sequence[dict[str, Any]], system_text: str | None = None,
        on_text_delta=None, cancellation_requested=None, lease_token: str | None = None,
    ) -> dict[str, Any]:
        if not isinstance(request_id, str) or not request_id.strip() or len(request_id) > 160:
            raise ValueError("request_id must be non-empty text of at most 160 characters.")
        if type(expected_version) is not int or expected_version < 0:
            raise ValueError("expected_version must be a non-negative integer.")
        if not isinstance(user_text, str) or not user_text.strip() or len(user_text) > 20_000:
            raise ValueError("user_text must be non-empty text of at most 20000 characters.")
        if not isinstance(tools, (list, tuple)) or len(tools) > 32:
            raise ValueError("tools must contain at most 32 definitions.")

        with self._conversation_lock((owner_id, conversation_id)):
            starting_version = self.store.get_conversation(
                owner_id=owner_id, conversation_id=conversation_id
            )["version"]
            recovered_request_ids = set()
            # Reconcile durable running records before accepting retries. Never
            # re-execute a model/tool sequence after a process crash: side effects
            # may already have happened before the crash.
            pending = self.store.list_interrupted_generations(
                owner_id=owner_id, conversation_id=conversation_id, limit=100
            )
            for generation in pending:
                recovered_request_ids.add(generation["request_id"])
                conversation_now = self.store.get_conversation(
                    owner_id=owner_id, conversation_id=conversation_id
                )
                messages_now = self.store.list_messages(
                    owner_id=owner_id, conversation_id=conversation_id, limit=200
                )
                user_message = next(
                    (item for item in messages_now if item["message_id"] == generation["user_message_id"]),
                    None,
                )
                following = self.store.list_messages(
                    owner_id=owner_id, conversation_id=conversation_id,
                    after_sequence=user_message["sequence"] if user_message else 0,
                    limit=1,
                ) if user_message else []
                if following and following[0]["role"] == "assistant":
                    self.store.finish_generation(
                        owner_id=owner_id, conversation_id=conversation_id,
                        request_id=generation["request_id"], status="completed", lease_token=lease_token,
                    )
                elif user_message is not None:
                    self.store.append_message(
                        owner_id=owner_id, conversation_id=conversation_id,
                        role="assistant",
                        content="This response was interrupted before completion. Please submit a new request; Yuki will not automatically repeat potentially side-effecting actions.",
                        idempotency_key="assistant:" + generation["request_id"],
                        expected_version=conversation_now["version"], generation_lease_token=lease_token,
                    )
                    self.store.finish_generation(
                        owner_id=owner_id, conversation_id=conversation_id,
                        request_id=generation["request_id"], status="interrupted", lease_token=lease_token,
                    )
            # Check ownership and stale versions before invoking a model.
            conversation = self.store.get_conversation(
                owner_id=owner_id, conversation_id=conversation_id
            )
            if (recovered_request_ids and request_id not in recovered_request_ids
                    and expected_version == starting_version):
                # Recovery appended the missing assistant turn. Advance this new
                # request's optimistic version by exactly those reconciled writes.
                expected_version = conversation["version"]
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
                expected_version=expected_version, generation_request_id=request_id,
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
            self.store.update_generation_checkpoint(
                owner_id=owner_id, conversation_id=conversation_id,
                request_id=request_id, checkpoint={"phase": "model_running"}, lease_token=lease_token,
            )
            try:
                kwargs = {"tools": list(tools), "history": history}
                if system_text is not None:
                    kwargs["system_text"] = system_text
                execution_loop = self.agent_loop
                if self.agent_loop_for_owner is not None:
                    execution_loop = self.agent_loop_for_owner(owner_id)
                    if not isinstance(execution_loop, AgentExecutionLoop):
                        raise TypeError("owner agent-loop factory returned an invalid execution loop")
                    if execution_loop is self.agent_loop:
                        raise PermissionError("owner agent-loop factory must not return the shared default loop")
                    with self._loop_owners_guard:
                        try:
                            previous_owner = self._loop_owners.get(execution_loop)
                            if previous_owner is not None and previous_owner != owner_id:
                                raise PermissionError("an agent execution loop cannot be shared across owners")
                            self._loop_owners[execution_loop] = owner_id
                        except TypeError as exc:
                            raise TypeError("owner agent loops must support weak identity tracking") from exc
                # The model-visible tool catalog must never advertise handlers
                # that are absent from this authenticated owner's guarded runtime.
                # This prevents a caller-supplied schema from exposing another
                # owner's tools or creating a misleading authorization surface.
                protocol = getattr(execution_loop, "tool_protocol", None)
                registered_names = getattr(protocol, "registered_tool_names", None)
                if self.agent_loop_for_owner is not None and registered_names is not None:
                    advertised_names = set()
                    for definition in tools:
                        if not isinstance(definition, dict):
                            raise ValueError("tool definitions must be objects")
                        function = definition.get("function")
                        name = function.get("name") if isinstance(function, dict) else None
                        if not isinstance(name, str) or not name:
                            raise ValueError("tool definition name is invalid")
                        advertised_names.add(name)
                    if advertised_names != set(registered_names):
                        raise PermissionError("model tool catalog does not match owner guarded runtime")
                result = execution_loop.run(
                    user_text, on_text_delta=on_text_delta,
                    cancellation_requested=cancellation_requested, **kwargs,
                )
                status = result.status
                model_turns = result.model_turns
                tool_calls = result.tool_calls
                if result.status == "completed":
                    answer = result.text
                elif result.status == "cancelled":
                    answer = "Generation was stopped before completion."
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
                expected_version=user_result["version"], generation_lease_token=lease_token,
            )
            self.store.finish_generation(
                owner_id=owner_id, conversation_id=conversation_id,
                request_id=request_id,
                status=status if status in {"completed", "failed", "cancelled"} else "failed",
                lease_token=lease_token,
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
